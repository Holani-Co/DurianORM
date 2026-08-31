class Api::V1::Accounts::WhatsappConsentsController < Api::V1::Accounts::BaseController
  before_action :check_authorization

  def index
    @whatsapp_consents = Current.account.whatsapp_consents.includes(:contact, :inbox).latest_first
    @whatsapp_consents = @whatsapp_consents.where(inbox_id: params[:inbox_id]) if params[:inbox_id].present?
    @whatsapp_consents = @whatsapp_consents.where(contact_id: params[:contact_id]) if params[:contact_id].present?
  end

  def create
    inbox = Current.account.inboxes.find(consent_params[:inbox_id])
    contact = find_contact
    @whatsapp_consent = Current.account.whatsapp_consents.create!(
      consent_params.slice(:status, :details).merge(
        inbox: inbox,
        contact: contact,
        purpose: 'MARKETING',
        source: 'manual_dashboard',
        recorded_at: Time.current
      )
    )
  end

  private

  def consent_params
    params.require(:whatsapp_consent).permit(
      :inbox_id, :contact_id, :phone_number, :status, details: {}
    )
  end

  def find_contact
    return Current.account.contacts.find(consent_params[:contact_id]) if consent_params[:contact_id].present?

    Current.account.contacts.find_by!(phone_number: consent_params[:phone_number])
  end
end
