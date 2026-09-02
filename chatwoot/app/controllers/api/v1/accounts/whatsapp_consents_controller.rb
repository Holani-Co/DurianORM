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

  # Bulk opt-in: upload a CSV of numbers, tag them with a label, and record
  # MARKETING OPTED_IN for the inbox — processed async by WhatsappConsentImportJob.
  def import
    inbox = Current.account.inboxes.find(params[:inbox_id])
    error = import_validation_error(inbox)
    return render json: { error: error }, status: :unprocessable_entity if error

    data_import = Current.account.data_imports.new(data_type: 'whatsapp_consent_optin', inbox_id: inbox.id, label: params[:label])
    data_import.import_file.attach(params[:import_file])
    data_import.save!
    head :ok
  end

  private

  def import_validation_error(inbox)
    return 'Select a WhatsApp inbox' unless inbox.channel_type == 'Channel::Whatsapp'
    return 'A CSV file is required' if params[:import_file].blank?
    return 'A label is required' if params[:label].blank?
    return 'Unknown label' unless Current.account.labels.exists?(title: params[:label])

    nil
  end

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
