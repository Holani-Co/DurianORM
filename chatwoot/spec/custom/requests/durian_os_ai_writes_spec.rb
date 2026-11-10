require 'rails_helper'
require Rails.root.join('spec/custom/support/durian_os_ai_helpers')

# AI writes through the whole stack (docs/mcp/MCP.md, sections 4 to 6 and 11.3): offered only to a pass with Full access
# (`administer`) and only where ORM's own policies let that person do it, through the controller action the screen
# uses. Made-up people only.
RSpec.describe 'Durian OS AI writes', type: :request do
  include ActiveJob::TestHelper
  include DurianOsAiHelpers

  let(:registry) { DurianOs::Ai::Registry }
  let(:tara_sub) { '0b6c2f4e-7d1a-4c55-9e0a-3f1d2c4b5a69' }
  let(:omar_sub) { '5d0e9a1c-2b3f-4a6d-8e7f-1a2b3c4d5e6f' }
  let(:nora_sub) { '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d' }
  let!(:account) { create(:account, name: 'Durian') }
  let(:other_account) { create(:account, name: 'Elsewhere') }
  let!(:tara) do
    create(:user, account: account, role: :administrator, name: 'Tara Fern', display_name: 'Tara', email: 'tara.fern@orm.test',
                  custom_attributes: { 'os_person_id' => tara_sub })
  end
  let!(:omar) do
    create(:user, account: account, role: :agent, name: 'Omar Quill', display_name: 'Omar', email: 'omar.quill@orm.test',
                  custom_attributes: { 'os_person_id' => omar_sub })
  end
  let!(:nora) do
    create(:user, account: account, role: :agent, name: 'Nora Pike', display_name: 'Nora', email: 'nora.pike@orm.test',
                  custom_attributes: { 'os_person_id' => nora_sub })
  end
  let!(:sales) { create(:inbox, account: account, name: 'WhatsApp Sales') }
  let!(:service) { create(:inbox, account: account, name: 'Email Service') }
  let!(:sales_chat) { create(:conversation, account: account, inbox: sales) }
  let!(:service_chat) { create(:conversation, account: account, inbox: service) }
  let(:v1) { "/api/v1/accounts/#{account.id}" }
  let(:lines) { [] }
  let(:read_only_words) do
    'This connection is read-only, so it cannot change anything. Reconnect with Full access in Durian OS to make changes.'
  end

  around { |example| with_modified_env(ai_env) { example.run } }

  before do
    allow(DurianOs::Config).to receive(:account_id).and_return(account.id)
    create(:inbox_member, user: omar, inbox: sales)
    create(:inbox_member, user: nora, inbox: sales)
    create(:inbox_member, user: tara, inbox: sales)
    allow(DurianOs::Ai::Log).to receive(:write).and_wrap_original do |original, fields|
      lines << DurianOs::Ai::Log.ordered(fields)
      original.call(fields)
    end
  end

  def pass_for(sub, **)
    ai_pass(sub: sub, **)
  end

  def ai_write(verb, path, pass, body = nil, headers: {})
    public_send(verb, path, params: body, as: :json, headers: ai_headers(pass).merge(headers))
  end

  def error
    response.parsed_body['error']
  end

  def ai_get_me(pass)
    get '/api/v1/ai/me', headers: ai_headers(pass)
  end

  def path_of(entry, ids = {})
    entry.path.gsub(/:(\w+)/) { |_| Regexp.last_match(1) == 'account_id' ? account.id : (ids[Regexp.last_match(1).to_sym] || 1) }
  end

  def agent_role(user, permissions)
    role = create(:custom_role, account: account, permissions: permissions)
    user.account_users.find_by(account: account).update!(custom_role: role)
  end

  describe 'a pass below Full access' do
    it 'refuses every offered write, even called by name, and changes nothing' do
      before_counts = [Conversation, Contact, Label, CannedResponse, Message, Note].map(&:count)
      registry.offered_writes.each do |entry|
        [[tara_sub, 'view'], [tara_sub, 'edit'], [omar_sub, 'edit']].each do |sub, level|
          ai_write(entry.http_method.downcase, path_of(entry), pass_for(sub, max_level: level), {})
          expect(response).to have_http_status(:forbidden), "#{entry.name} #{level}"
          expect(error).to eq(read_only_words), entry.name
          expect(lines.last).to include('refused' => 'read_only', 'tool' => entry.name, 'write' => 'refused', 'status' => 403)
        end
      end
      expect([Conversation, Contact, Label, CannedResponse, Message, Note].map(&:count)).to eq(before_counts)
    end

    it 'refuses a working write the same way, an administrator at edit and an agent at view' do
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority", pass_for(tara_sub, max_level: 'edit'), { priority: 'low' })
      expect([response.status, error]).to eq([403, read_only_words])
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_status", pass_for(omar_sub, max_level: 'view'),
               { status: 'pending' })
      expect([response.status, error]).to eq([403, read_only_words])
      expect(sales_chat.reload.priority).to be_nil
      expect(sales_chat).to be_open
    end

    it 'treats a missing or unknown level as read-only' do
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority", pass_for(tara_sub, max_level: 'root'), { priority: 'low' })
      expect(error).to eq(read_only_words)
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority", pass_for(tara_sub, claims: { max_level: DurianOsAiHelpers::OMIT }), { priority: 'low' })
      expect(error).to eq(read_only_words)
      expect(sales_chat.reload.priority).to be_nil
    end

    it 'does not list a write in ai/me, and says it is read-only' do
      ai_get_me(pass_for(tara_sub, max_level: 'edit'))
      expect(response.parsed_body).to include('read_only' => true)
      expect(response.parsed_body['tools'] & registry.offered_writes.map(&:name)).to be_empty
    end
  end

  describe 'a Full connection' do
    def note_by_tara(text)
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(tara_sub), { content: text, private: true })
    end

    it 'lists the writes the person may call, by role, with the feature on' do
      account.enable_features!('companies')
      ai_get_me(pass_for(tara_sub))
      expect(response.parsed_body).to include('read_only' => false)
      expect(response.parsed_body['tools']).to include('add_private_note', 'create_label', 'merge_contacts', 'create_company')
      expect(response.parsed_body['tools']).not_to include('run_macro')

      ai_get_me(pass_for(omar_sub))
      expect(response.parsed_body['tools']).to include('add_private_note', 'create_canned_response', 'merge_contacts')
      expect(response.parsed_body['tools']).not_to include('create_label', 'update_label')

      account.disable_features!('companies')
      ai_get_me(pass_for(tara_sub))
      expect(response.parsed_body['tools']).not_to include('create_company')
      expect(response.parsed_body['tools']).not_to include('send_message', 'delete_conversation', 'start_conversation')
    end

    describe 'conversations' do
      it 'refuses to mute (Chatwoot resolves the conversation and blocks the contact), and unmutes, sets priority, custom attributes, labels and participants' do
        id = sales_chat.display_id
        ai_write(:post, "#{v1}/conversations/#{id}/mute", pass_for(tara_sub))
        expect([response.status, lines.last['refused']]).to eq([403, 'write_refused'])
        expect(sales_chat.reload.priority).to be_nil
        expect(sales_chat).to be_open

        ai_write(:post, "#{v1}/conversations/#{id}/unmute", pass_for(tara_sub))
        expect(response).to have_http_status(:ok)
        expect(lines.last).to include('tool' => 'unmute_conversation', 'write' => 'done', 'status' => 200, 'ceiling' => 'administer')

        ai_write(:post, "#{v1}/conversations/#{id}/toggle_priority", pass_for(tara_sub), { priority: 'urgent' })
        expect(sales_chat.reload.priority).to eq('urgent')

        create(:custom_attribute_definition, account: account, attribute_key: 'order_number', attribute_display_name: 'Order number',
                                             attribute_model: :conversation_attribute)
        ai_write(:post, "#{v1}/conversations/#{id}/custom_attributes", pass_for(tara_sub), { custom_attributes: { order_number: 'A-1042' } })
        expect(response).to have_http_status(:ok)
        expect(sales_chat.reload.custom_attributes).to include('order_number' => 'A-1042')

        create(:label, account: account, title: 'vip')
        ai_write(:post, "#{v1}/conversations/#{id}/labels", pass_for(tara_sub), { labels: ['vip'] })
        expect(response).to have_http_status(:ok)
        expect(sales_chat.reload.label_list).to eq(['vip'])

        ai_write(:post, "#{v1}/conversations/#{id}/participants", pass_for(tara_sub), { user_ids: [omar.id] })
        expect(sales_chat.conversation_participants.pluck(:user_id)).to eq([omar.id])
        ai_write(:delete, "#{v1}/conversations/#{id}/participants", pass_for(tara_sub), { user_ids: [omar.id] })
        expect(response).to have_http_status(:ok)
        expect(sales_chat.conversation_participants.count).to eq(0)
      end

      it 'does not touch the bridge\'s keys in custom attributes, or invent a label' do
        sales_chat.update!(custom_attributes: { 'zoho_tickets' => [{ 'id' => '1' }] })
        ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/custom_attributes", pass_for(tara_sub),
                 { custom_attributes: { zoho_tickets: [] } })
        expect(response).to have_http_status(:unprocessable_entity)
        expect(error).to include('zoho_tickets')
        expect(sales_chat.reload.custom_attributes['zoho_tickets']).to eq([{ 'id' => '1' }])
        expect(lines.last).to include('refused' => 'bad_input', 'write' => 'refused')

        ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/labels", pass_for(tara_sub), { labels: ['made-up'] })
        expect(response).to have_http_status(:unprocessable_entity)
        expect(sales_chat.reload.label_list).to be_empty
      end

      it 'sets pending, assigns and sets priority, and the activity says who did it and via which AI' do
        id = sales_chat.display_id
        perform_enqueued_jobs do
          ai_write(:post, "#{v1}/conversations/#{id}/toggle_status", pass_for(tara_sub, client: 'Claude'), { status: 'pending' })
          expect(response).to have_http_status(:ok)
          ai_write(:post, "#{v1}/conversations/#{id}/assignments", pass_for(tara_sub, client: 'Claude'), { assignee_id: omar.id })
          expect(response).to have_http_status(:ok)
          ai_write(:post, "#{v1}/conversations/#{id}/toggle_priority", pass_for(tara_sub, client: 'Claude'), { priority: 'low' })
        end
        expect(sales_chat.reload).to be_pending
        expect(sales_chat.assignee).to eq(omar)
        activity = sales_chat.messages.where(message_type: :activity).pluck(:content)
        expect(activity).to include(a_string_including('Tara Fern via Claude'))
        expect(activity.grep(/via Claude/).size).to be >= 3
        expect(activity.join).not_to include('via Claude via Claude')
      end

      it 'leaves a browser change without the mark' do
        perform_enqueued_jobs do
          post "#{v1}/conversations/#{sales_chat.display_id}/toggle_status", params: { status: 'resolved' }, headers: tara.create_new_auth_token
        end
        expect(sales_chat.messages.where(message_type: :activity).pluck(:content).join).not_to include('via')
      end

      it 'adds a private note marked as the AI\'s, and never sends a customer a reply' do
        note_by_tara('Called the customer back.')
        expect(response).to have_http_status(:ok)
        message = sales_chat.messages.order(:id).last
        expect(message).to be_private
        expect(message).to be_outgoing
        expect([message.content, message.sender]).to eq(['Called the customer back.', tara])
        expect(message.content_attributes).to include('via_ai' => 'Claude')
        expect(lines.last).to include('tool' => 'add_private_note', 'write' => 'done')

        count = sales_chat.messages.count
        [{ content: 'Hello!', private: false }, { content: 'Hello!' }, { content: 'Hello!', private: 'true' },
         { content: 'Hello!', private: true, attachments: ['x'] }, { content: 'Hello!', private: true, message_type: 'incoming' }].each do |body|
          ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(tara_sub), body)
          expect(response).to have_http_status(:forbidden), body.inspect
        end
        expect(sales_chat.messages.count).to eq(count)
        ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(tara_sub), { content: 'Hello!', private: false })
        expect(error).to eq('An AI connection can only add private notes to a conversation. Replies and other messages that reach a customer ' \
                            'stay in ORM for now.')
        expect(lines.last).to include('refused' => 'write_refused', 'write' => 'refused')
      end

      it 'takes only a JSON body of the tool\'s own fields and no query' do
        path = "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority"
        post "#{path}.json?priority=high", params: { priority: 'urgent' }.to_json,
                                          headers: ai_headers(pass_for(tara_sub)).merge('CONTENT_TYPE' => 'application/json')
        expect([response.status, lines.last['refused']]).to eq([403, 'bad_input'])
        ai_write(:post, path, pass_for(tara_sub), { priority: 'urgent', sla_policy_id: 1 })
        expect([response.status, lines.last['refused']]).to eq([403, 'bad_input'])
        post path, params: 'priority=urgent', headers: ai_headers(pass_for(tara_sub))
        expect([response.status, lines.last['refused']]).to eq([403, 'bad_input'])
        post path, params: [1], as: :json, headers: ai_headers(pass_for(tara_sub))
        expect([response.status, lines.last['refused']]).to eq([403, 'bad_input'])
        expect(sales_chat.reload.priority).to be_nil
      end

      it 'is refused a second verb or path the registry does not name' do
        ai_write(:put, "#{v1}/contacts/1", pass_for(tara_sub), { name: 'X' })
        expect(response).to have_http_status(:forbidden)
        expect(lines.last).to include('refused' => 'not_a_tool')
      end
    end

    describe 'contacts, companies and saved replies' do
      let!(:contact) { create(:contact, account: account, name: 'Nina Lark', email: 'nina.lark@orm.test') }

      it 'creates, updates, labels, notes and removes attributes' do
        ai_write(:post, "#{v1}/contacts", pass_for(omar_sub), { name: 'Kavi Rao', email: 'kavi.rao@orm.test', phone_number: '+919800000001' })
        expect(response).to have_http_status(:ok)
        expect(account.contacts.find_by(email: 'kavi.rao@orm.test').name).to eq('Kavi Rao')

        ai_write(:patch, "#{v1}/contacts/#{contact.id}", pass_for(omar_sub), { name: 'Nina Moss' })
        expect(contact.reload.name).to eq('Nina Moss')
        ai_write(:patch, "#{v1}/contacts/#{contact.id}", pass_for(omar_sub), { custom_attributes: { crm_contact_id: '9' } })
        expect(response).to have_http_status(:unprocessable_entity)
        ai_write(:post, "#{v1}/contacts", pass_for(omar_sub), { name: 'Bad', avatar_url: 'http://169.254.169.254/x' })
        expect(response).to have_http_status(:forbidden)

        create(:label, account: account, title: 'vip')
        ai_write(:post, "#{v1}/contacts/#{contact.id}/labels", pass_for(omar_sub), { labels: ['vip'] })
        expect(contact.reload.label_list).to eq(['vip'])

        ai_write(:post, "#{v1}/contacts/#{contact.id}/notes", pass_for(omar_sub), { content: 'Prefers calls after 5.' })
        expect(response).to have_http_status(:ok)
        note = contact.notes.last
        expect([note.content, note.user]).to eq(['Prefers calls after 5.', omar])
        ai_write(:patch, "#{v1}/contacts/#{contact.id}/notes/#{note.id}", pass_for(omar_sub), { content: 'Prefers calls after 6.' })
        expect(note.reload.content).to eq('Prefers calls after 6.')
        ai_write(:delete, "#{v1}/contacts/#{contact.id}/notes/#{note.id}", pass_for(omar_sub))
        expect(contact.notes.count).to eq(0)

        create(:custom_attribute_definition, account: account, attribute_key: 'city_tier', attribute_display_name: 'City tier',
                                             attribute_model: :contact_attribute)
        contact.update!(custom_attributes: { 'city_tier' => '1', 'crm_contact_id' => '9' })
        ai_write(:post, "#{v1}/contacts/#{contact.id}/destroy_custom_attributes", pass_for(omar_sub), { custom_attributes: ['city_tier'] })
        expect(contact.reload.custom_attributes).to eq('crm_contact_id' => '9')
        ai_write(:post, "#{v1}/contacts/#{contact.id}/destroy_custom_attributes", pass_for(omar_sub), { custom_attributes: ['crm_contact_id'] })
        expect(response).to have_http_status(:unprocessable_entity)
      end

      it 'merges two contacts' do
        other = create(:contact, account: account, name: 'N. Lark')
        ai_write(:post, "#{v1}/actions/contact_merge", pass_for(omar_sub), { base_contact_id: contact.id, mergee_contact_id: other.id })
        expect(response).to have_http_status(:ok)
        expect(Contact.exists?(other.id)).to be(false)
        expect(lines.last).to include('tool' => 'merge_contacts', 'write' => 'done')
      end

      it 'never deletes a contact or a company, even for an administrator' do
        account.enable_features!('companies')
        company = create(:company, account: account)
        ai_write(:delete, "#{v1}/contacts/#{contact.id}", pass_for(tara_sub))
        expect([response.status, lines.last['refused']]).to eq([403, 'write_refused'])
        expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['deletion'])
        ai_write(:delete, "#{v1}/companies/#{company.id}", pass_for(tara_sub))
        expect(response).to have_http_status(:forbidden)
        expect([Contact.exists?(contact.id), Company.exists?(company.id)]).to eq([true, true])
      end

      it 'creates and changes a company, and links a contact, with the feature on' do
        account.enable_features!('companies')
        ai_write(:post, "#{v1}/companies", pass_for(omar_sub), { name: 'Fern Interiors', domain: 'fern.test' })
        expect(response).to have_http_status(:ok)
        company = account.companies.find_by(name: 'Fern Interiors')
        ai_write(:patch, "#{v1}/companies/#{company.id}", pass_for(omar_sub), { description: 'Dealer' })
        expect(company.reload.description).to eq('Dealer')
        ai_write(:post, "#{v1}/companies/#{company.id}/contacts", pass_for(omar_sub), { contact_id: contact.id })
        expect(contact.reload.company_id).to eq(company.id)
        ai_write(:delete, "#{v1}/companies/#{company.id}/contacts/#{contact.id}", pass_for(omar_sub))
        expect(contact.reload.company_id).to be_nil
      end

      it 'creates, changes and deletes a canned response; labels are for administrators' do
        ai_write(:post, "#{v1}/canned_responses", pass_for(omar_sub), { short_code: 'hours', content: 'We open at 10.' })
        expect(response).to have_http_status(:ok)
        reply = account.canned_responses.find_by(short_code: 'hours')
        ai_write(:patch, "#{v1}/canned_responses/#{reply.id}", pass_for(omar_sub), { content: 'We open at 11.' })
        expect(reply.reload.content).to eq('We open at 11.')
        ai_write(:delete, "#{v1}/canned_responses/#{reply.id}", pass_for(omar_sub))
        expect(CannedResponse.exists?(reply.id)).to be(false)

        ai_write(:post, "#{v1}/labels", pass_for(omar_sub), { title: 'refund' })
        expect([response.status, lines.last['refused'], error]).to eq([403, 'admin_only', DurianOs::Ai::ADMIN_ONLY_WRITE])
        expect(account.labels.where(title: 'refund')).to be_empty
        ai_write(:post, "#{v1}/labels", pass_for(tara_sub), { title: 'refund', color: '#1f93ff', description: 'Refund asked' })
        expect(response).to have_http_status(:ok)
        label = account.labels.find_by(title: 'refund')
        ai_write(:patch, "#{v1}/labels/#{label.id}", pass_for(tara_sub), { description: 'Wants money back' })
        expect(label.reload.description).to eq('Wants money back')
        ai_write(:delete, "#{v1}/labels/#{label.id}", pass_for(tara_sub))
        expect(response).to have_http_status(:forbidden)
        expect(Label.exists?(label.id)).to be(true)
      end
    end

    describe 'macros' do
      before { create(:label, account: account, title: 'vip') }

      it 'refuses to run a macro, even one that only adds a label: a macro can send, and its run can resolve' do
        macro = create(:macro, account: account, created_by: tara, visibility: :global, name: 'Tag',
                               actions: [{ 'action_name' => 'add_label', 'action_params' => ['vip'] }])
        [tara_sub, omar_sub].each do |sub|
          perform_enqueued_jobs do
            ai_write(:post, "#{v1}/macros/#{macro.id}/execute", pass_for(sub), { conversation_ids: [sales_chat.display_id] })
          end
          expect([response.status, lines.last['refused']]).to eq([403, 'write_refused'])
          expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['customer'])
        end
        expect(sales_chat.reload.label_list).to be_empty
        expect(enqueued_jobs.pluck('job_class')).not_to include('MacrosExecutionJob')
      end
    end

    describe 'what can reach a customer' do
      let(:status_path) { "#{v1}/conversations/#{sales_chat.display_id}/toggle_status" }
      let(:chat_path) { "#{v1}/conversations/#{sales_chat.display_id}" }

      it 'never resolves a conversation (the satisfaction survey), however the status is sent' do
        [{ status: 'resolved' }, {}, { status: '' }, { status: nil }, { status: ['resolved'] }, { status: 'bot' }].each do |body|
          ai_write(:post, status_path, pass_for(tara_sub), body)
          expect([response.status, lines.last['refused']]).to eq([403, 'bad_input']), body.inspect
        end
        ai_write(:post, status_path, pass_for(omar_sub), { status: 'resolved', snoozed_until: '2026-12-01T09:00:00Z' })
        expect(response).to have_http_status(:forbidden)
        expect(sales_chat.reload).to be_open
        expect(sales_chat.messages.count).to eq(0)
      end

      it 'sets open, pending and snoozed' do
        %w[pending snoozed open].each do |status|
          ai_write(:post, status_path, pass_for(tara_sub), { status: status })
          expect(response).to have_http_status(:ok), status
          expect(sales_chat.reload.status).to eq(status)
        end
      end

      it 'does not clear labels when the field is left out, and refuses a note without content' do
        create(:label, account: account, title: 'vip')
        sales_chat.update_labels(['vip'])
        [{}, { labels: nil }, { labels: 'vip' }].each do |body|
          ai_write(:post, "#{chat_path}/labels", pass_for(tara_sub), body)
          expect([response.status, lines.last['refused']]).to eq([403, 'bad_input']), body.inspect
        end
        ai_write(:post, "#{chat_path}/messages", pass_for(tara_sub), { private: true })
        expect(response).to have_http_status(:forbidden)
        expect(sales_chat.reload.label_list).to eq(['vip'])
      end

      it 'takes values of the declared type only' do
        ai_write(:post, "#{chat_path}/toggle_priority", pass_for(tara_sub), { priority: 'critical' })
        expect(response).to have_http_status(:forbidden)
        ai_write(:post, "#{chat_path}/participants", pass_for(tara_sub), { user_ids: ['1'] })
        expect(response).to have_http_status(:forbidden)
        ai_write(:post, "#{chat_path}/messages", pass_for(tara_sub), { content: 'x' * 5001, private: true })
        expect(response).to have_http_status(:forbidden)
        expect(sales_chat.reload.priority).to be_nil
      end

      it 'assigns to an agent or a team of the account, never takes the assignment away, never hands it to the bot' do
        bot = create(:user, account: account, role: :agent, name: 'DurianAI', email: 'durian.ai@orm.test')
        create(:inbox_member, user: bot, inbox: sales)
        team = create(:team, account: account)
        [omar, nora].each { |agent| create(:team_member, team: team, user: agent) }
        sales_chat.update!(assignee: omar, team: team)
        path = "#{chat_path}/assignments"
        [{}, { assignee_id: nil }, { team_id: nil }, { assignee_id: 0 }, { assignee_id: 999_999 }, { assignee_id: '1' },
         { assignee_id: omar.id, assignee_type: 'AgentBot' }, { team_id: 999_999 }].each do |body|
          ai_write(:post, path, pass_for(tara_sub), body)
          # the gate's own checks (a stray field, a wrong type) answer 403; the assignment guard answers 422
          gate = body[:assignee_type] || body[:assignee_id] == '1'
          expect([response.status, lines.last['refused']]).to eq([gate ? 403 : 422, 'bad_input']), body.inspect
        end
        ai_write(:post, path, pass_for(tara_sub), { assignee_id: bot.id })
        expect([response.status, lines.last['refused']]).to eq([403, 'write_refused'])
        expect(error).to include('DurianAI')
        expect(sales_chat.reload).to have_attributes(assignee: omar, team: team)

        ai_write(:post, path, pass_for(tara_sub), { assignee_id: nora.id })
        expect(sales_chat.reload.assignee).to eq(nora)
        ai_write(:post, path, pass_for(tara_sub), { team_id: team.id })
        expect(response).to have_http_status(:ok)
      end

      it 'sends no reply to the customer when a private note is added' do
        ActionMailer::Base.deliveries.clear
        perform_enqueued_jobs do
          ai_write(:post, "#{chat_path}/messages", pass_for(tara_sub), { content: 'Hi', private: true })
        end
        expect(response).to have_http_status(:ok)
        expect(sales_chat.messages.where(private: false, message_type: :outgoing).count).to eq(0)
        # Chatwoot queues SendReplyJob for every outgoing message, a private note included; the channel services send nothing for it.
        expect(ActionMailer::Base.deliveries).to be_empty
        expect(a_request(:any, /.*/)).not_to have_been_made
      end
    end

    describe 'by role' do
      it 'keeps an agent inside their inboxes' do
        ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority", pass_for(omar_sub), { priority: 'low' })
        expect(response).to have_http_status(:ok)

        ai_write(:post, "#{v1}/conversations/#{service_chat.display_id}/toggle_priority", pass_for(omar_sub), { priority: 'low' })
        expect([response.status, lines.last['refused']]).to eq([403, 'role'])
        expect(error).to eq('You are not authorized to do this action')
        ai_write(:post, "#{v1}/conversations/#{service_chat.display_id}/messages", pass_for(omar_sub), { content: 'note' })
        expect(response).to have_http_status(:forbidden)
        ai_write(:post, "#{v1}/conversations/#{service_chat.display_id}/toggle_status", pass_for(omar_sub), { status: 'pending' })
        expect(response).to have_http_status(:forbidden)
        expect(service_chat.reload.priority).to be_nil
        expect(service_chat).to be_open
        expect(service_chat.messages.where(private: true)).to be_empty
      end

      it 'lets an administrator write in every inbox' do
        ai_write(:post, "#{v1}/conversations/#{service_chat.display_id}/toggle_priority", pass_for(tara_sub), { priority: 'low' })
        expect(response).to have_http_status(:ok)
        expect(service_chat.reload.priority).to eq('low')
      end

      it 'follows a custom role: only the conversations it covers' do
        agent_role(nora, ['conversation_participating_manage'])
        sales.update!(enable_auto_assignment: false)
        mine = create(:conversation, account: account, inbox: sales)
        mine.update_columns(assignee_id: nora.id) # rubocop:disable Rails/SkipsModelValidations
        theirs = create(:conversation, account: account, inbox: sales)

        ai_write(:post, "#{v1}/conversations/#{mine.display_id}/toggle_priority", pass_for(nora_sub), { priority: 'low' })
        expect(response).to have_http_status(:ok)
        ai_write(:post, "#{v1}/conversations/#{theirs.display_id}/toggle_priority", pass_for(nora_sub), { priority: 'low' })
        expect([response.status, lines.last['refused']]).to eq([403, 'role'])
        expect(theirs.reload.priority).to be_nil

        ai_write(:post, "#{v1}/conversations/#{mine.display_id}/toggle_priority", pass_for(nora_sub, max_level: 'edit'), { priority: 'low' })
        expect(error).to eq(read_only_words)
      end

      it 'is refused another account, even for a member' do
        other = create(:conversation, account: other_account)
        create(:account_user, account: other_account, user: tara, role: :administrator)
        ai_write(:post, "/api/v1/accounts/#{other_account.id}/conversations/#{other.display_id}/toggle_priority", pass_for(tara_sub), { priority: 'low' })
        expect([response.status, lines.last['refused']]).to eq([403, 'wrong_account'])
        expect(other.reload.priority).to be_nil
        ai_write(:post, "/api/v1/accounts/#{other_account.id}/labels", pass_for(tara_sub), { title: 'x' })
        expect(response).to have_http_status(:forbidden)
        expect(other_account.labels).to be_empty
      end
    end
  end

  describe 'the writes that stay in the screens' do
    it 'refuses every refused write on a Full connection, in its reason\'s words' do
      registry.refused_writes.each do |entry|
        ai_write(entry.http_method.split('|').first.downcase, path_of(entry), pass_for(tara_sub), {})
        expect(response).to have_http_status(:forbidden), entry.name
        expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS.fetch(entry.reason)), entry.name
        expect(lines.last).to include('refused' => 'write_refused', 'tool' => entry.name, 'write' => 'refused')
      end
    end

    it 'refuses a customer-facing send on a Full connection, and nothing is sent or changed' do
      count = Message.count
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/transcript", pass_for(tara_sub), { email: 'x@orm.test' })
      expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['customer'])
      ai_write(:post, "#{v1}/campaigns", pass_for(tara_sub), { title: 'Sale' })
      expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['customer'])
      ai_write(:post, "#{v1}/integrations/zoho_bridge/create_ticket", pass_for(tara_sub), {})
      expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['outside'])
      ai_write(:post, "#{v1}/bulk_actions", pass_for(tara_sub), { type: 'Conversation', ids: [1], fields: { status: 'resolved' } })
      expect(error).to eq(DurianOs::Ai::Registry::WRITE_REFUSED_REASONS['bulk'])
      expect(Message.count).to eq(count)
    end

    it 'never reaches access, tokens, secrets or settings, with or without Full access' do
      ai_write(:post, "#{v1}/agents", pass_for(tara_sub), { email: 'new.agent@orm.test', name: 'New', role: 'administrator' })
      expect(lines.last).to include('refused' => 'web_only')
      ai_write(:post, '/api/v1/profile/reset_access_token', pass_for(tara_sub))
      expect(lines.last).to include('refused' => 'web_only')
      ai_write(:post, "#{v1}/webhooks", pass_for(tara_sub), { url: 'https://elsewhere.test/hook' })
      expect(lines.last).to include('refused' => 'web_only')
      expect(User.where(email: 'new.agent@orm.test')).to be_empty
    end
  end

  describe 'reads stay read-only' do
    it 'still wraps a read of a Full connection in while_preventing_writes' do
      allow_any_instance_of(Api::V1::Accounts::LabelsController).to receive(:index) do # rubocop:disable RSpec/AnyInstance
        Current.account.update!(name: 'Changed by a read')
      end
      get "#{v1}/labels", headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'would_write')
      expect(account.reload.name).to eq('Durian')
    end

    it 'refuses a write action reached through a read route by another verb' do
      post "#{v1}/labels/#{create(:label, account: account).id}", params: { _method: 'delete' }, headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
    end

    it 'runs a write without the guard only when the gate let it through' do
      ai_write(:post, "#{v1}/conversations/#{sales_chat.display_id}/toggle_priority", pass_for(tara_sub), { priority: 'low' })
      expect(response).to have_http_status(:ok)
      # the next read on the same thread is guarded again
      expect { ActiveRecord::Base.while_preventing_writes { sales_chat.update!(priority: 'low') } }.to raise_error(ActiveRecord::ReadOnlyError)
    end
  end

  describe 'the audit trail' do
    it 'marks an audit row written during an AI request with the AI\'s name' do
      audit = Audited::Audit.new(comment: 'renamed')
      DurianOs::Current.set(ai: DurianOs::Ai::Caller.new(user: tara, sub: tara_sub, client: 'Claude', ceiling: 'administer', ref: 'Ab3dEf')) do
        DurianOs::Ai.mark_audit(audit)
      end
      expect(audit.comment).to eq('via Claude (AI connection) · renamed')
      plain = Audited::Audit.new(comment: 'renamed')
      DurianOs::Ai.mark_audit(plain)
      expect(plain.comment).to eq('renamed')
    end
  end
end
