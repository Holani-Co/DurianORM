require 'rails_helper'
require Rails.root.join('spec/custom/support/durian_os_ai_helpers')

# The Zoho bridge proxy forwards the conversation it authorized. Rails takes conversation_id from the query string
# before the body, and the bridge reads the conversation the body names with its own ORM token, so a body naming
# another conversation must never reach it. Made-up people only.
RSpec.describe 'Zoho bridge proxy', type: :request do
  include DurianOsAiHelpers

  let(:omar_sub) { '5d0e9a1c-2b3f-4a6d-8e7f-1a2b3c4d5e6f' }
  let!(:account) { create(:account, name: 'Durian') }
  let!(:omar) do
    create(:user, account: account, role: :agent, name: 'Omar Quill', email: 'omar.quill@orm.test',
                  custom_attributes: { 'os_person_id' => omar_sub })
  end
  let!(:sales) { create(:inbox, account: account, name: 'WhatsApp Sales') }
  let!(:service) { create(:inbox, account: account, name: 'Email Service') }
  let!(:sales_chat) { create(:conversation, account: account, inbox: sales) }
  let!(:service_chat) { create(:conversation, account: account, inbox: service) }
  let(:base) { "/api/v1/accounts/#{account.id}/integrations/zoho_bridge" }
  let(:sent) { [] }

  before do
    create(:inbox_member, user: omar, inbox: sales)
    allow(HTTParty).to receive(:post) do |url, options|
      sent << [url, JSON.parse(options[:body])]
      instance_double(HTTParty::Response, parsed_response: { 'ok' => true }, code: 200)
    end
  end

  # The query names the conversation Omar can open (sales), the body another inbox's (service).
  def post_mismatched(action, headers)
    post "#{base}/#{action}?conversation_id=#{sales_chat.display_id}",
         params: { conversation_id: service_chat.display_id, to_emails: 'someone@orm.test' }.to_json,
         headers: headers.merge('Content-Type' => 'application/json')
  end

  it 'forwards the conversation it checked for every action, whatever the body names' do
    actions = %w[resolve_ticket_decision regenerate_review_reply escalate_review resolve_category_decision create_crm_deal
                 contact_deals draft_ticket create_ticket forward_recipients forward_email]
    actions.each do |action|
      post_mismatched(action, omar.create_new_auth_token)
      expect(response).to have_http_status(:ok), action
      expect(sent.last.last['conversation_id']).to eq(sales_chat.display_id), action
    end
    expect(sent.size).to eq(actions.size)
  end

  it 'keeps the rest of the body' do
    body = { conversation_id: sales_chat.display_id, choice: 'merge', target_ticket_id: 42 }
    post "#{base}/resolve_ticket_decision", params: body.to_json, headers: omar.create_new_auth_token.merge('Content-Type' => 'application/json')
    expect(response).to have_http_status(:ok)
    expect(sent.last).to eq(['http://127.0.0.1:8420/chatwoot/resolve-ticket-decision',
                             { 'conversation_id' => sales_chat.display_id, 'choice' => 'merge', 'target_ticket_id' => 42 }])
  end

  it 'refuses a conversation the person cannot open, and sends nothing' do
    path = "#{base}/contact_deals?conversation_id=#{service_chat.display_id}"
    headers = omar.create_new_auth_token.merge('Content-Type' => 'application/json')
    post path, params: { conversation_id: sales_chat.display_id }.to_json, headers: headers
    expect(response).to have_http_status(:unauthorized)
    expect(sent).to be_empty
  end

  it 'forwards the conversation it checked for an AI pass too' do
    with_modified_env(ai_env) do
      allow(DurianOs::Config).to receive(:account_id).and_return(account.id)
      post_mismatched('contact_deals', ai_headers(ai_pass(sub: omar_sub, max_level: 'view')))
    end
    expect(response).to have_http_status(:ok)
    expect(sent.last.last['conversation_id']).to eq(sales_chat.display_id)
  end
end
