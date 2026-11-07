require 'rails_helper'
require Rails.root.join('spec/custom/support/durian_os_ai_helpers')

# Durian OS AI connections through the whole stack (docs/mcp/MCP.md, sections 1 to 10). Made-up people only.
RSpec.describe 'Durian OS AI connections', type: :request do
  include DurianOsAiHelpers

  let(:tara_sub) { '0b6c2f4e-7d1a-4c55-9e0a-3f1d2c4b5a69' }
  let(:omar_sub) { '5d0e9a1c-2b3f-4a6d-8e7f-1a2b3c4d5e6f' }
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
  let!(:sales) { create(:inbox, account: account, name: 'WhatsApp Sales') }
  let!(:service) { create(:inbox, account: account, name: 'Email Service') }
  let!(:sales_chat) { create(:conversation, account: account, inbox: sales) }
  let!(:service_chat) { create(:conversation, account: account, inbox: service) }
  let(:v1) { "/api/v1/accounts/#{account.id}" }
  let(:v2) { "/api/v2/accounts/#{account.id}" }
  let(:lines) { [] }

  around { |example| with_modified_env(ai_env) { example.run } }

  before do
    allow(DurianOs::Config).to receive(:account_id).and_return(account.id)
    create(:inbox_member, user: omar, inbox: sales)
    create(:inbox_member, user: tara, inbox: service)
    allow(DurianOs::Ai::Log).to receive(:write).and_wrap_original do |original, fields|
      lines << DurianOs::Ai::Log.ordered(fields)
      original.call(fields)
    end
  end

  def pass_for(sub, **)
    ai_pass(sub: sub, **)
  end

  def ai_get(path, pass, params: {}, headers: {})
    get path, params: params, headers: headers.merge(ai_headers(pass))
  end

  def conversation_ids
    response.parsed_body.dig('data', 'payload').pluck('id')
  end

  def error
    response.parsed_body['error']
  end

  describe 'an agent' do
    it 'sees only the conversations of their own inboxes' do
      ai_get("#{v1}/conversations", pass_for(omar_sub, max_level: 'edit'), params: { status: 'all' })
      expect(response).to have_http_status(:ok)
      expect(conversation_ids).to eq([sales_chat.display_id])

      ai_get("#{v1}/conversations/#{sales_chat.display_id}", pass_for(omar_sub))
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['id']).to eq(sales_chat.display_id)

      ai_get("#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(omar_sub))
      expect(response).to have_http_status(:ok)
    end

    it 'is refused another inbox\'s conversation with 403, never 401' do
      ai_get("#{v1}/conversations/#{service_chat.display_id}", pass_for(omar_sub))
      expect(response).to have_http_status(:forbidden)
      expect(error).to eq('You are not authorized to do this action')
      expect(lines.last).to include('refused' => 'role', 'status' => 403)

      ai_get("#{v1}/conversations/#{service_chat.display_id}/messages", pass_for(omar_sub))
      expect(response).to have_http_status(:forbidden)

      ai_get("#{v1}/inboxes/#{service.id}", pass_for(omar_sub))
      expect(response).to have_http_status(:forbidden)
    end

    it 'finds only their inboxes\' conversations by search' do
      sales_chat.contact.update!(name: 'Nina Lark')
      service_chat.contact.update!(name: 'Nina Moss')
      # The search answer shows each conversation's first message (the view fails without one, in the browser too).
      [sales_chat, service_chat].each { |chat| create(:message, account: account, inbox: chat.inbox, conversation: chat) }
      ai_get("#{v1}/search/conversations", pass_for(omar_sub), params: { q: 'Nina' })
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body.dig('payload', 'conversations').pluck('id')).to eq([sales_chat.display_id])
    end

    it 'keeps their custom role' do
      role = create(:custom_role, account: account, permissions: ['conversation_participating_manage'])
      omar.account_users.find_by(account: account).update!(custom_role: role)
      mine = create(:conversation, account: account, inbox: sales, assignee: omar)

      ai_get("#{v1}/conversations", pass_for(omar_sub, max_level: 'administer'), params: { status: 'all', assignee_type: 'all' })
      expect(conversation_ids).to eq([mine.display_id])

      ai_get('/api/v1/ai/me', pass_for(omar_sub, max_level: 'administer'))
      expect(response.parsed_body).to include('role' => 'agent', 'acts_as' => 'agent',
                                              'custom_role' => { 'id' => role.id, 'name' => role.name })
      expect(response.parsed_body['permissions']).to include('conversation_participating_manage', 'custom_role')
    end

    it 'reaches reports with a report_manage custom role, and still not admin tools' do
      role = create(:custom_role, account: account, permissions: ['report_manage'])
      omar.account_users.find_by(account: account).update!(custom_role: role)

      ai_get("#{v2}/reports/summary", pass_for(omar_sub), params: { type: 'account', since: 7.days.ago.to_i, until: Time.now.to_i })
      expect(response).to have_http_status(:ok)

      ai_get("#{v1}/campaigns", pass_for(omar_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'admin_only', 'tool' => 'list_campaigns')
    end

    it 'is refused reports without report access' do
      ai_get("#{v2}/reports/summary", pass_for(omar_sub), params: { type: 'account', since: 7.days.ago.to_i, until: Time.now.to_i })
      expect(response).to have_http_status(:forbidden)
      expect(error).to eq('This needs report access in ORM.')
      expect(lines.last).to include('refused' => 'no_report_access')
    end
  end

  describe 'an administrator' do
    it 'sees every conversation at administer' do
      ai_get("#{v1}/conversations", pass_for(tara_sub, max_level: 'administer'), params: { status: 'all' })
      expect(conversation_ids).to contain_exactly(sales_chat.display_id, service_chat.display_id)

      ai_get("#{v1}/campaigns", pass_for(tara_sub, max_level: 'administer'))
      expect(response).to have_http_status(:ok)

      ai_get("#{v2}/reports/summary", pass_for(tara_sub, max_level: 'administer'),
             params: { type: 'account', since: 7.days.ago.to_i, until: Time.now.to_i })
      expect(response).to have_http_status(:ok)
    end

    %w[edit view act nonsense].each do |level|
      it "acts as a plain agent at #{level}" do
        ai_get("#{v1}/conversations", pass_for(tara_sub, max_level: level), params: { status: 'all' })
        expect(response).to have_http_status(:ok)
        expect(conversation_ids).to eq([service_chat.display_id])

        ai_get("#{v1}/conversations/#{sales_chat.display_id}", pass_for(tara_sub, max_level: level))
        expect(response).to have_http_status(:forbidden)

        ai_get("#{v1}/campaigns", pass_for(tara_sub, max_level: level))
        expect(response).to have_http_status(:forbidden)
        expect(error).to eq('Only an ORM administrator can read this, on a Durian OS connection with full access.')

        ai_get("#{v2}/reports/summary", pass_for(tara_sub, max_level: level),
               params: { type: 'account', since: 7.days.ago.to_i, until: Time.now.to_i })
        expect(response).to have_http_status(:forbidden)

        expect(tara.account_users.first.reload.role).to eq('administrator')
      end
    end

    it 'acts as a plain agent below administer even with a custom role' do
      role = create(:custom_role, account: account, permissions: %w[conversation_manage report_manage])
      tara.account_users.find_by(account: account).update!(custom_role: role)

      ai_get("#{v2}/reports/summary", pass_for(tara_sub, max_level: 'edit'),
             params: { type: 'account', since: 7.days.ago.to_i, until: Time.now.to_i })
      expect(response).to have_http_status(:forbidden)

      ai_get('/api/v1/ai/me', pass_for(tara_sub, max_level: 'edit'))
      expect(response.parsed_body).to include('role' => 'administrator', 'acts_as' => 'agent', 'permissions' => ['agent'])
    end

    it 'never sees an inbox secret or a WhatsApp provider_config, which the browser shows it' do
      api_inbox = create(:channel_api, account: account).inbox
      whatsapp = create(:channel_whatsapp, account: account, sync_templates: false, validate_provider_config: false).inbox

      get "#{v1}/inboxes/#{api_inbox.id}", headers: tara.create_new_auth_token
      expect(response.parsed_body).to include('secret', 'hmac_token')
      get "#{v1}/inboxes/#{whatsapp.id}", headers: tara.create_new_auth_token
      expect(response.parsed_body).to include('provider_config')

      ai_get("#{v1}/inboxes/#{api_inbox.id}", pass_for(tara_sub))
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body).not_to include('secret', 'hmac_token')
      ai_get("#{v1}/inboxes/#{whatsapp.id}", pass_for(tara_sub))
      expect(response.body).not_to include('provider_config', 'test_key')

      ai_get("#{v1}/inboxes", pass_for(tara_sub))
      expect(response.body).not_to include('provider_config', 'test_key', '"secret"', '"hmac_token"')
    end
  end

  describe 'the answer' do
    it 'replaces signed file links and keeps the file details' do
      create(:message, :with_attachment, account: account, inbox: sales, conversation: sales_chat)
      ai_get("#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(tara_sub))
      attachment = response.parsed_body['payload'].flat_map { |message| message['attachments'] || [] }.first
      expect(attachment).to include('data_url' => '(file: open it in ORM)', 'file_type' => 'image')
      expect(response.body).not_to include('/rails/active_storage/')
    end

    context 'with a provider CDN link, which opens with no sign-in' do
      # ORM's Channel::Instagram also subscribes to comments, which the factory's stub does not cover.
      before { stub_request(:post, %r{\Ahttps://graph\.instagram\.com/.+/subscribed_apps}) }

      def note
        '(file: open it in ORM)'
      end

      def fb_link
        'https://scontent.xx.fbcdn.net/v/t1.15752-9/photo.jpg?_nc_cat=1&oh=00_AbCSigned&oe=6700AAAA'
      end

      def first_attachment(body)
        body['payload'].flat_map { |message| message['attachments'] || [] }.first
      end

      it 'replaces the link of an incoming Instagram file kept in ORM, in messages and in attachment lists' do
        ig_chat = create(:conversation, account: account, inbox: create(:channel_instagram, account: account).inbox)
        message = create(:message, :with_attachment, account: account, inbox: ig_chat.inbox, conversation: ig_chat, message_type: :incoming)
        message.attachments.first.update!(external_url: 'https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=17999&signature=AbCdEfSigned')
        get "#{v1}/conversations/#{ig_chat.display_id}/messages", headers: tara.create_new_auth_token
        expect(response.body).to include('lookaside.fbsbx.com')

        ai_get("#{v1}/conversations/#{ig_chat.display_id}/messages", pass_for(tara_sub))
        expect(first_attachment(response.parsed_body)).to include('data_url' => note, 'thumb_url' => note, 'file_type' => 'image',
                                                                  'extension' => 'png', 'file_size' => be_positive)
        ai_get("#{v1}/conversations/#{ig_chat.display_id}/attachments", pass_for(tara_sub))
        expect(response.parsed_body['payload'].first).to include('data_url' => note, 'thumb_url' => note, 'file_type' => 'image')
        ai_get("#{v1}/contacts/#{ig_chat.contact_id}/attachments", pass_for(tara_sub))
        expect(response.parsed_body['payload'].first).to include('data_url' => note, 'thumb_url' => note)
        ai_get("#{v1}/conversations/#{ig_chat.display_id}", pass_for(tara_sub))
        expect(response).to have_http_status(:ok)
        expect(response.body).not_to include('fbsbx.com', 'signature=')
      end

      it 'replaces the link of a file never downloaded, a story and an ad, and keeps a plain link' do
        message = create(:message, account: account, inbox: sales, conversation: sales_chat, message_type: :incoming)
        message.attachments.create!(account_id: account.id, file_type: :image, external_url: fb_link)
        referral = { headline: 'Sofa sale', image_url: fb_link, source_url: 'https://durian.in/sofas' }
        create(:message, account: account, inbox: sales, conversation: sales_chat, message_type: :incoming, content: 'Loved this',
                         content_attributes: { story_url: fb_link, referral: referral })

        ai_get("#{v1}/conversations/#{sales_chat.display_id}/messages", pass_for(tara_sub))
        expect(response).to have_http_status(:ok)
        expect(response.body).not_to include('fbcdn.net', 'oh=00_')
        expect(first_attachment(response.parsed_body)).to include('data_url' => note, 'thumb_url' => '', 'file_type' => 'image')
        story = response.parsed_body['payload'].find { |item| item['content'] == 'Loved this' }
        expect(story['content_attributes']).to include('story_url' => note)
        expect(story['content_attributes']['referral']).to eq(referral.stringify_keys.merge('image_url' => note))
      end
    end

    it 'never sends a campaign media signed id, which opens the file for good' do
      campaign = create(:campaign, account: account, inbox: sales)
      blob = ActiveStorage::Blob.create_and_upload!(io: StringIO.new('promo'), filename: 'promo.jpg', content_type: 'image/jpeg')
      ActiveStorage::Attachment.create!(name: 'media', record: campaign, blob: blob)

      get "#{v1}/campaigns", headers: tara.create_new_auth_token
      signed_id = response.parsed_body.first['media_signed_id']
      expect(signed_id).to be_present

      ai_get("#{v1}/campaigns", pass_for(tara_sub))
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body.first).to include('media_filename' => 'promo.jpg')
      expect(response.body).not_to include('media_signed_id', signed_id)
    end

    it 'sends no session token and leaves users.tokens alone' do
      tara.create_new_auth_token
      tokens = tara.reload.tokens
      ai_get("#{v1}/conversations", pass_for(tara_sub))
      expect(response).to have_http_status(:ok)
      expect(response.headers.keys.map(&:downcase)).not_to include('access-token', 'client', 'uid', 'expiry', 'authorization', 'set-cookie')
      expect(tara.reload.tokens).to eq(tokens)
    end

    it 'never stores the year in review, which the browser does' do
      ai_get("#{v2}/year_in_review", pass_for(tara_sub), params: { year: 2025 })
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['year'].to_s).to eq('2025')
      expect(tara.reload.ui_settings.to_h.keys).not_to include("year_in_review_#{account.id}_2025")

      get "#{v2}/year_in_review", params: { year: 2025 }, headers: tara.create_new_auth_token
      expect(tara.reload.ui_settings.to_h.keys).to include("year_in_review_#{account.id}_2025")
    end

    it 'refuses a read that would write, and nothing is written' do
      allow_any_instance_of(Api::V1::Accounts::LabelsController).to receive(:index) do # rubocop:disable RSpec/AnyInstance
        Current.account.update!(name: 'Changed by a read')
      end
      ai_get("#{v1}/labels", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(error).to eq('This read would change data in ORM, so an AI connection cannot run it yet.')
      expect(lines.last).to include('refused' => 'would_write', 'tool' => 'list_labels')
      expect(account.reload.name).to eq('Durian')
    end

    it 'refuses a read that answers with a file' do
      allow_any_instance_of(Api::V1::Accounts::LabelsController).to receive(:index) do |controller| # rubocop:disable RSpec/AnyInstance
        controller.send(:send_data, 'a,b', type: 'text/csv', filename: 'labels.csv')
      end
      ai_get("#{v1}/labels", pass_for(tara_sub))
      expect(response).to have_http_status(:bad_gateway)
      expect(lines.last).to include('refused' => 'not_json')
    end
  end

  describe 'the gate' do
    it 'refuses a write for every ceiling, and nothing changes' do
      messages = sales_chat.messages.count
      %w[view edit administer].each do |level|
        post "#{v1}/conversations/#{sales_chat.display_id}/messages", params: { content: 'Hello' }.to_json,
                                                                      headers: ai_headers(pass_for(tara_sub, max_level: level))
                                                                        .merge('Content-Type' => 'application/json')
        expect(response).to have_http_status(:forbidden)
        expect(error).to eq("ORM's AI connection only reads for now, so it cannot change anything: make this change in ORM itself.")
        expect(lines.last).to include('refused' => 'read_only', 'tool' => 'send_message', 'ceiling' => level)
      end
      expect(sales_chat.messages.count).to eq(messages)

      patch "/api/v1/accounts/#{account.id}", params: { name: 'Renamed' }, headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      delete "#{v1}/conversations/#{sales_chat.display_id}", headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      post "#{v1}/conversations/#{sales_chat.display_id}", params: { _method: 'delete' }, headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(account.reload.name).to eq('Durian')
      expect(sales_chat.reload).to be_present
    end

    it 'refuses browser-only routes with their reason' do
      ai_get('/api/v1/profile', pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(error).to start_with("This isn't available to AI connections. It shows or resets an access token")
      expect(lines.last).to include('refused' => 'web_only')

      post "#{v1}/agents", params: { name: 'New Person', email: 'new.person@orm.test' }, headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'web_only')
      expect(User.from_email('new.person@orm.test')).to be_nil

      ai_get("#{v1}/agent_bots", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
    end

    it 'refuses an unlisted route and a path with no route' do
      allow(DurianOs::Ai::Registry).to receive(:entry_for).and_call_original
      allow(DurianOs::Ai::Registry).to receive(:entry_for).with('api/v1/accounts/labels#index').and_return(nil)
      ai_get("#{v1}/labels", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'not_a_tool')

      ai_get("#{v1}/no_such_thing", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(error).to eq("This isn't available to AI connections.")
    end

    it 'refuses a read reached another way: HEAD, POST, another format' do
      head "#{v1}/conversations", headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'not_a_tool', 'method' => 'HEAD')

      ai_get("#{v1}/conversations.csv", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'not_a_tool')

      post "#{v1}/conversations/meta", headers: ai_headers(pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
    end

    it 'refuses mounted apps' do
      ['/cable', '/monitoring/sidekiq', '/rails/active_storage/blobs/redirect/abc/file.png', '/bot'].each do |path|
        ai_get(path, pass_for(tara_sub))
        expect(response).to have_http_status(:forbidden)
        expect(lines.last).to include('status' => 403)
      end
    end

    it 'refuses another account, even for its members' do
      create(:account_user, account: other_account, user: tara, role: :administrator)
      ai_get("/api/v1/accounts/#{other_account.id}/conversations", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(error).to eq("ORM's AI connection works in the Durian account only.")
      expect(lines.last).to include('refused' => 'wrong_account')

      ai_get("/api/v1/accounts/#{other_account.id}", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
    end

    it 'checks again what the controller runs' do
      labels = DurianOs::Ai::Registry.entry_for('api/v1/accounts/labels#index')
      decision = DurianOs::Ai::Gate::Decision.new(key: labels.key, entry: labels, route: 'GET /labels')
      allow_any_instance_of(DurianOs::Ai::Gate).to receive(:decide!).and_return(decision) # rubocop:disable RSpec/AnyInstance
      ai_get("#{v1}/conversations", pass_for(tara_sub))
      expect(response).to have_http_status(:forbidden)
      expect(lines.last).to include('refused' => 'not_a_tool')
    end
  end

  describe 'the pass' do
    it 'refuses a bad signature, an unknown key, another issuer, audience or type, and an expired pass' do
      passes = [
        pass_for(tara_sub, key: OpenSSL::PKey.generate_key('ED25519')),
        pass_for(tara_sub, header: { kid: 'retired-key' }),
        pass_for(tara_sub, claims: { iss: 'https://evil.test' }),
        pass_for(tara_sub, claims: { aud: 'pat:studio' }),
        pass_for(tara_sub, claims: { typ: 'handoff' }),
        pass_for(tara_sub, now: Time.now.to_i - 200)
      ]
      passes.each do |pass|
        ai_get("#{v1}/conversations", pass)
        expect(response).to have_http_status(:unauthorized)
        expect(response.headers['WWW-Authenticate']).to eq('Bearer')
        expect(error).to eq('This Durian OS connection is not valid. Reconnect in Durian OS.')
      end
      expect(lines.pluck('refused')).to eq(%w[bad_signature unknown_key wrong_issuer wrong_audience wrong_type expired])
    end

    it 'is used once' do
      pass = pass_for(tara_sub)
      ai_get("#{v1}/conversations", pass)
      expect(response).to have_http_status(:ok)
      ai_get("#{v1}/conversations", pass)
      expect(response).to have_http_status(:unauthorized)
      expect(lines.last).to include('refused' => 'replayed')
    end

    it 'is refused while the switch is off, and the browser is untouched' do
      with_modified_env('ORM_OS_PASS' => 'off') do
        ai_get("#{v1}/conversations", pass_for(tara_sub))
        expect(response).to have_http_status(:unauthorized)
        expect(lines.last).to include('refused' => 'not_configured')

        get "#{v1}/conversations", headers: tara.create_new_auth_token
        expect(response).to have_http_status(:ok)
      end
    end

    it 'refuses two Authorization headers' do
      get "#{v1}/conversations", headers: { 'Authorization' => "Bearer #{pass_for(tara_sub)}, Bearer #{pass_for(tara_sub)}" }
      expect(response).to have_http_status(:unauthorized)
      expect(lines.last).to include('refused' => 'malformed')
    end

    it 'ignores cookies, session headers and another user\'s API token, and never falls back to them' do
      others = omar.create_new_auth_token.merge('api_access_token' => omar.access_token.token, 'Cookie' => 'session=other')
      get '/api/v1/ai/me', headers: others.merge(ai_headers(pass_for(tara_sub)))
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['user']).to eq('tara.fern@orm.test')

      get '/api/v1/ai/me', headers: others.merge(ai_headers(pass_for(tara_sub, key: OpenSSL::PKey.generate_key('ED25519'))))
      expect(response).to have_http_status(:unauthorized)
    end
  end

  describe 'the person' do
    it 'is refused when ORM does not know them' do
      ai_get("#{v1}/conversations", pass_for(SecureRandom.uuid))
      expect(response).to have_http_status(:unauthorized)
      expect(lines.last).to include('refused' => 'unknown_person')
    end

    it 'is refused for service and emergency users and offboarded people' do
      [{ 'os_kind' => 'service' }, { 'os_kind' => 'emergency' }, { 'os_offboarded_at' => '2026-11-01T00:00:00Z' }].each do |extra|
        omar.update!(custom_attributes: { 'os_person_id' => omar_sub }.merge(extra))
        ai_get("#{v1}/conversations", pass_for(omar_sub))
        expect(response).to have_http_status(:unauthorized)
        expect(lines.last).to include('refused' => 'no_account')
      end
    end

    it 'is refused without a row in the managed account' do
      outsider = create(:user, account: other_account, role: :administrator, email: 'ira.vale@orm.test',
                               custom_attributes: { 'os_person_id' => '9f8e7d6c-5b4a-4321-8fed-cba987654321' })
      ai_get("#{v1}/conversations", pass_for(outsider.os_person_id))
      expect(response).to have_http_status(:unauthorized)
      expect(lines.last).to include('refused' => 'no_account')
    end
  end

  describe 'load' do
    it 'refuses a third request of one person at once with 429, without spending the pass' do
      keys = (0..1).map { |n| "orm_os_pass:slot:p:#{omar_sub}:#{n}" }
      keys.each { |key| Redis::Alfred.set(key, 'held', nx: true, ex: 60) }
      pass = pass_for(omar_sub)
      ai_get("#{v1}/conversations", pass)
      expect(response).to have_http_status(:too_many_requests)
      expect(response.headers['Retry-After']).to eq('2')
      expect(error).to eq('ORM is answering other AI requests. Try again in a moment.')
      expect(lines.last).to include('refused' => 'person_busy')

      keys.each { |key| Redis::Alfred.delete(key) }
      ai_get("#{v1}/conversations", pass)
      expect(response).to have_http_status(:ok)
      expect(Redis::Alfred.exists?(keys.first)).to be(false)
    end

    it 'refuses past the limit in all' do
      with_modified_env('ORM_AI_CONCURRENCY' => '1') do
        Redis::Alfred.set('orm_os_pass:slot:all:0', 'held', nx: true, ex: 60)
        ai_get("#{v1}/conversations", pass_for(tara_sub))
        expect(response).to have_http_status(:too_many_requests)
        expect(lines.last).to include('refused' => 'busy')
        expect(Redis::Alfred.exists?("orm_os_pass:slot:p:#{tara_sub}:0")).to be(false)
        Redis::Alfred.delete('orm_os_pass:slot:all:0')
      end
    end

    it 'fails closed when Redis is down' do
      allow($alfred).to receive(:with).and_raise(Redis::CannotConnectError) # rubocop:disable Style/GlobalVars
      ai_get("#{v1}/conversations", pass_for(tara_sub))
      expect(response).to have_http_status(:service_unavailable)
      expect(lines.last).to include('refused' => 'redis')
    end
  end

  describe 'the log line' do
    it 'names the person, the tool and the route pattern, never the pass, the path or the query' do
      pass = pass_for(tara_sub, client: 'Claude')
      ai_get("#{v1}/conversations/search", pass, params: { q: 'Nina Lark' })
      line = lines.last
      expect(line).to include('ai' => 'pass', 'user' => 'tara.fern@orm.test', 'user_id' => tara.id, 'client' => 'Claude',
                              'sub' => tara_sub, 'method' => 'GET', 'tool' => 'find_conversations_with_text',
                              'route' => 'GET /api/v1/accounts/:account_id/conversations/search', 'status' => 200,
                              'ref' => ai_jti(pass).first(6))
      expect(line).not_to have_key('refused')
      text = line.to_json
      expect(text).not_to include(pass, ai_jti(pass), 'Nina', "/accounts/#{account.id}/")
    end
  end

  describe 'GET /api/v1/ai/me' do
    it 'says who a pass acts as and what it may call' do
      ai_get('/api/v1/ai/me', pass_for(tara_sub, max_level: 'edit', client: 'Claude'))
      expect(response).to have_http_status(:ok)
      body = response.parsed_body
      expect(body).to include('user' => 'tara.fern@orm.test', 'name' => 'Tara Fern', 'user_id' => tara.id, 'os_person_id' => tara_sub,
                              'auth_kind' => 'pass', 'client' => 'Claude', 'ceiling' => 'edit', 'role' => 'administrator',
                              'custom_role' => nil, 'acts_as' => 'agent', 'permissions' => ['agent'], 'read_only' => true,
                              'account' => { 'id' => account.id, 'name' => 'Durian' })
      expect(body['inboxes']).to eq([{ 'id' => service.id, 'name' => 'Email Service', 'channel_type' => 'Channel::WebWidget' }])
      expect(body['tools']).to include('get_my_orm_access', 'list_conversations', 'get_conversation', 'list_messages')
      expect(body['tools']).not_to include('list_campaigns', 'get_report_summary')
      expect(body['tools']).to eq(body['tools'].sort)

      ai_get('/api/v1/ai/me', pass_for(tara_sub, max_level: 'administer'))
      expect(response.parsed_body).to include('acts_as' => 'administrator', 'permissions' => ['administrator'])
      expect(response.parsed_body['inboxes'].pluck('id')).to contain_exactly(sales.id, service.id)
      expect(response.parsed_body['tools']).to include('list_campaigns', 'get_report_summary')
    end

    it 'lists a feature tool only while the feature is on' do
      account.enable_features!('help_center')
      ai_get('/api/v1/ai/me', pass_for(tara_sub))
      expect(response.parsed_body['tools']).to include('list_help_center_portals')

      account.disable_features!('help_center')
      ai_get('/api/v1/ai/me', pass_for(tara_sub))
      expect(response.parsed_body['tools']).not_to include('list_help_center_portals')
    end

    it 'answers a browser session in the same shape' do
      get '/api/v1/ai/me', headers: omar.create_new_auth_token
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body).to include('auth_kind' => 'session', 'client' => nil, 'ceiling' => nil, 'role' => 'agent',
                                              'acts_as' => 'agent', 'read_only' => false)
      expect(response.parsed_body['inboxes'].pluck('id')).to eq([sales.id])
      expect(lines).to be_empty
    end
  end

  it 'is the last middleware in the stack' do
    expect(Rails.application.middleware.last.klass).to eq(DurianOsAiDoor)
  end
end
