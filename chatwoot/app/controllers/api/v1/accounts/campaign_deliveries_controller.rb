class Api::V1::Accounts::CampaignDeliveriesController < Api::V1::Accounts::BaseController
  RESULTS_PER_PAGE = 100

  before_action :check_authorization
  before_action :campaign

  def index
    deliveries = @campaign.campaign_deliveries.includes(:contact).latest_first
    deliveries = deliveries.where(status: params[:status]) if params[:status].present?
    deliveries = search(deliveries) if params[:q].present?

    @campaign_deliveries = deliveries.page(params[:page]).per(RESULTS_PER_PAGE)
  end

  def export
    send_data delivery_csv,
              filename: "whatsapp-campaign-#{@campaign.display_id}-deliveries-#{Date.current}.csv",
              type: 'text/csv'
  end

  private

  def campaign
    @campaign = Current.account.campaigns.find_by!(display_id: params[:campaign_id])
  end

  def search(deliveries)
    query = "%#{ActiveRecord::Base.sanitize_sql_like(params[:q].strip)}%"
    deliveries.left_joins(:contact).where(
      'contacts.name ILIKE :query OR campaign_deliveries.phone_number ILIKE :query OR ' \
      'campaign_deliveries.skip_reason ILIKE :query OR campaign_deliveries.error_message ILIKE :query',
      query: query
    )
  end

  def delivery_csv
    CSV.generate do |csv|
      csv << ['Contact', 'Phone number', 'Status', 'Skip reason', 'Attempts', 'Queued at', 'Sent at',
              'Delivered at', 'Read at', 'Replied at', 'Failed at', 'Error code', 'Error message', 'Meta message ID']
      @campaign.campaign_deliveries.includes(:contact).find_each do |delivery|
        csv << delivery_row(delivery).map { |value| safe_csv_cell(value) }
      end
    end
  end

  def delivery_row(delivery)
    [
      delivery.contact.name, delivery.phone_number, delivery.status, delivery.skip_reason, delivery.attempt_count,
      delivery.queued_at&.iso8601, delivery.sent_at&.iso8601, delivery.delivered_at&.iso8601,
      delivery.read_at&.iso8601, delivery.replied_at&.iso8601, delivery.failed_at&.iso8601,
      delivery.error_code, delivery.error_message, delivery.meta_message_id
    ]
  end

  def safe_csv_cell(value)
    string = value.to_s
    string.match?(/\A[=+\-@]/) ? "'#{string}" : value
  end
end
