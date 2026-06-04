# frozen_string_literal: true

# DM Bot processor: handles incoming DMs on Facebook/Instagram inboxes.
# Sends a welcome option-menu on the first user message, routes based on
# the chosen option, calls OpenAI for AI-backed replies, and hands off to
# a human agent when needed.
#
# Setup (run once in Rails console):
#   bot = AgentBot.create!(name: 'DM Bot', outgoing_url: 'http://localhost:3000/internal/dm_bot/webhook')
#   # Assign to each inbox you want covered:
#   InboxAgentBot.find_or_create_by!(inbox: Inbox.find(<id>)).update!(agent_bot: bot, active: true)
#
# rubocop:disable Metrics/ClassLength
class Integrations::DmBot::ProcessorService < Integrations::BotProcessorService
  include Integrations::LlmInstrumentation

  pattr_initialize [:event_name!, :hook!, :event_data!]

  MENU_OPTIONS = [
    { title: '🛍️ Order Status',     value: 'order_status'    },
    { title: '📚 Manga / Products', value: 'products'        },
    { title: '🤖 Ask AI',           value: 'ask_ai'          },
    { title: '👤 Talk to a Human',  value: 'human'           }
  ].freeze

  # Public reply posted on a comment the AI flags as serious (abuse, scam/sue
  # accusations, legal threats). We always respond publicly AND hand to a
  # human, instead of silently handing off with no visible reply.
  COMMENT_HANDOFF_REPLY = "We're sorry to hear this 🙏 Please DM us so our team can look into it right away."

  private

  # Comments must be answered regardless of conversation status. All comments
  # on a post are grouped into ONE conversation that gets resolved/reopened/
  # assigned constantly, and the base class only runs while `pending` — which
  # made the bot reply to the first comment then fall silent until someone
  # manually resolved it. For comments we drop the pending gate so EVERY
  # comment gets a reply. DMs keep the gate (a human taking over a DM should
  # stop the bot).
  def should_run_processor?(message)
    return if message.private?
    return unless processable_message?(message)
    return true if comment_conversation?
    return unless conversation.pending?

    true
  end

  # Called by base class with (source_id, user_message_text)
  def get_response(_session_id, content)
    # Comments: skip menu entirely, just AI-reply (no option-select UI on public threads)
    return comment_reply(content) if comment_conversation?

    # On option selection the content is the submitted value string
    if event_name == 'message.updated'
      handle_option_selection(content)
    elsif first_message?
      :welcome_menu
    else
      ai_reply(content)
    end
  end

  def comment_conversation?
    type = event_data[:message].conversation.additional_attributes&.dig('type').to_s
    type.include?('comment')
  end

  def process_response(message, response)
    case response
    when :welcome_menu
      send_welcome_menu(message)
    when :handoff
      message.conversation.bot_handoff!
    when :resolve
      message.conversation.resolved!
    when Hash
      # `handoff: true` → post the public reply first, then hand to an agent.
      create_bot_message(message, response.except(:handoff))
      message.conversation.bot_handoff! if response[:handoff]
    when String
      create_bot_message(message, content: response)
    end
  end

  # ── Option routing ────────────────────────────────────────────────────────

  def handle_option_selection(value)
    case value
    when 'order_status'
      { content: "Please share your order ID and we'll look it up for you!" }
    when 'products'
      { content: 'Check out our latest manga and merch at our store! What would you like to know more about?' }
    when 'ask_ai'
      # Bot stays active — next message will go through ai_reply
      { content: "Sure! Go ahead and ask me anything. I'll do my best to help 🤖" }
    when 'human'
      :handoff
    else
      :welcome_menu
    end
  end

  # ── Welcome menu ──────────────────────────────────────────────────────────

  def send_welcome_menu(message)
    create_bot_message(message, {
                         content: 'Hey! 👋 Welcome to IComics / kisnemanga. How can I help you today?',
                         content_type: 'input_select',
                         content_attributes: {
                           items: MENU_OPTIONS
                         }
                       })
  end

  # ── AI reply ─────────────────────────────────────────────────────────────

  def ai_reply(user_content)
    return :handoff if user_content.blank?

    credential = llm_credential
    unless credential
      Rails.logger.warn('DmBot: no OpenAI API key configured, handing off')
      return :handoff
    end

    model = Llm::Config.configured_model
    result = generate_ai_response(credential, model, user_content)

    content = result[:message].to_s.strip
    # Escalate if the AI signals it can't help
    return :handoff if should_escalate?(content)

    { content: content }
  rescue StandardError => e
    Rails.logger.error("DmBot AI error: #{e.message}")
    send_failure_message(event_data[:message])
    :handoff
  end

  # Comment-specific reply. Unlike ai_reply (DMs), this ALWAYS produces a
  # public reply: ordinary comments get the AI's text; comments the AI flags
  # as serious (HANDOFF), a missing key, or an AI error all get a courteous
  # "please DM us" reply AND are handed to an agent — never a silent handoff.
  def comment_reply(user_content)
    return if user_content.blank?

    credential = llm_credential
    return { content: COMMENT_HANDOFF_REPLY, handoff: true } unless credential

    model = Llm::Config.configured_model
    result = generate_ai_response(credential, model, user_content)
    content = result[:message].to_s.strip

    return { content: COMMENT_HANDOFF_REPLY, handoff: true } if should_escalate?(content)

    { content: content }
  rescue StandardError => e
    Rails.logger.error("DmBot comment AI error: #{e.message}")
    { content: COMMENT_HANDOFF_REPLY, handoff: true }
  end

  # Runs the LLM turn inside a Langfuse generation span. Returns a hash shaped
  # like the other LLM services ({ message:, usage: }) so the shared
  # instrumentation helpers capture the completion content and token usage.
  def generate_ai_response(credential, model, user_content)
    instrument_llm_call(ai_instrumentation_params(model, user_content)) do
      Llm::Config.with_api_key(credential[:api_key]) do |ctx|
        chat = ctx.chat(model: model)
        chat.with_instructions(system_prompt)
        # Comments are standalone — a post's thread mixes many commenters, so
        # replaying it would confuse the model. Only replay history for DMs.
        replay_history(chat) unless comment_conversation?
        response = chat.ask(user_content)
        {
          message: response.content,
          usage: {
            'prompt_tokens' => response.input_tokens,
            'completion_tokens' => response.output_tokens,
            'total_tokens' => (response.input_tokens || 0) + (response.output_tokens || 0)
          }
        }
      end
    end
  end

  def ai_instrumentation_params(model, user_content)
    conversation = event_data[:message].conversation
    {
      span_name: 'llm.dm_bot_reply',
      account_id: conversation.account_id,
      conversation_id: conversation.display_id,
      feature_name: 'dm_bot',
      model: model,
      messages: [
        { role: 'system', content: system_prompt },
        { role: 'user', content: user_content }
      ],
      temperature: nil,
      metadata: {
        channel_type: conversation.inbox&.channel_type,
        is_comment: comment_conversation?
      }.compact
    }
  end

  # Replay prior turns so the LLM has conversation context (last 10 messages).
  def replay_history(chat)
    msgs = event_data[:message].conversation.messages
                               .where(message_type: %i[incoming outgoing])
                               .where(content_type: 'text')
                               .where.not(id: event_data[:message].id)
                               .order(:created_at)
                               .last(10)
    msgs.each do |m|
      role = m.incoming? ? :user : :assistant
      chat.add_message(role: role, content: m.content.to_s) if m.content.present?
    end
  end

  def send_failure_message(message)
    create_bot_message(message, content: "I'm having trouble right now. Let me connect you with a human! 🙏")
  rescue StandardError => e
    Rails.logger.error("DmBot failure-message error: #{e.message}")
  end

  def system_prompt
    comment_conversation? ? comment_system_prompt : dm_system_prompt
  end

  def comment_system_prompt
    <<~PROMPT
      You are the social-media voice of IComics / kisnemanga, an Indian manga and
      comics store, replying to a PUBLIC Instagram comment on one of our posts.

      ── HARD RULES ────────────────────────────────────────────────────────
      - Keep replies VERY short. Max ~12 words. One sentence ideally.
      - Stay 100% professional, brand-safe, friendly. NEVER anything NSFW,
        political, sarcastic, edgy, or controversial.
      - If the comment is just an emoji, react with one fitting emoji
        (optionally one or two warm words like "Thanks! 🙌").
      - If the comment is positive / appreciative / hype ("love this", "🔥",
        "amazing", "want one"), thank them warmly. Light emoji is fine.
      - If the comment is a question, concern, complaint, order/price/stock
        query, or anything that needs real back-and-forth, do NOT answer it
        here. Politely redirect to DM with something like:
          "Thanks for reaching out! Please DM us and we'll help you out 💌"
      - If the comment is a serious complaint, accusation, or legal threat
        ("you scammed me", "I'll sue", "fraud", "refund or else", mentions a
        lawyer), OR is abusive / NSFW / spam, respond with EXACTLY the single
        word: HANDOFF
      - Never mention prices, never quote stock, never make promises.
      - Never use hashtags. Never @-mention anyone.
    PROMPT
  end

  def dm_system_prompt
    <<~PROMPT
      You are a helpful customer support assistant for IComics / kisnemanga,
      a manga and comics store. Be friendly, concise, and use emojis sparingly.

      ── DEMO PRODUCT CATALOG ──────────────────────────────────────────────
      1. Attack on Titan — Vol. 1 (English)     — ₹499  (SKU: AOT-V1)
      2. One Piece — Vol. 1 (English)           — ₹549  (SKU: OP-V1)
      3. Demon Slayer — Complete Box Set (1-23) — ₹8,999 (SKU: DS-BOX)
      4. Naruto — Vol. 1 (English)              — ₹449  (SKU: NRT-V1)
      5. Berserk — Deluxe Edition Vol. 1        — ₹2,499 (SKU: BRK-DLX1)

      Shipping: Flat ₹99 across India. Free over ₹2,000. Delivery in 4-7 days.
      Returns: 7-day return window, item must be unopened.

      ── PLACING AN ORDER ──────────────────────────────────────────────────
      You CAN take orders directly in chat. To place an order, conversationally
      collect from the user (one or two items at a time, not a giant form):
        1. Which product(s) + quantity
        2. Full name
        3. Shipping address (with PIN code)
        4. Phone number

      Once you have all four, confirm the total (items + shipping) and reply
      with an order confirmation in this exact format on its own line:

        ✅ Order placed! Order ID: #DEMO-<random 5 digits>
        Items: <items>
        Total: ₹<amount>
        ETA: 4-7 days

      ── WHEN TO HAND OFF ──────────────────────────────────────────────────
      If the user asks for something outside this catalog (a product not listed,
      a custom request, refunds, payment issues, or anything you genuinely
      can't handle in chat), respond with EXACTLY the single word: HANDOFF
      Do not say HANDOFF for normal product/order questions — only when you
      truly need a human.
    PROMPT
  end

  def should_escalate?(content)
    content.strip.upcase == 'HANDOFF'
  end

  # ── Helpers ───────────────────────────────────────────────────────────────

  def first_message?
    # True when there are no prior outgoing bot messages in this conversation
    conversation = event_data[:message].conversation
    conversation.messages.where(message_type: :outgoing).none?
  end

  def create_bot_message(message, params)
    conv = message.conversation
    conv.messages.create!(
      {
        message_type: :outgoing,
        account_id: conv.account_id,
        inbox_id: conv.inbox_id,
        content_type: 'text'
      }.merge(params)
    )
  end

  def llm_credential
    # Prefer hook-level key, fall back to system key
    key = InstallationConfig.find_by(name: 'CAPTAIN_OPEN_AI_API_KEY')&.value
    return nil if key.blank?

    { api_key: key }
  end
end
# rubocop:enable Metrics/ClassLength
