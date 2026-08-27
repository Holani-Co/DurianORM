class Api::V1::Accounts::CampaignsController < Api::V1::Accounts::BaseController
  before_action :campaign, except: [:index, :create, :preview_audience, :test_message]
  before_action :check_authorization

  def index
    @campaigns = Current.account.campaigns
  end

  def show; end

  def create
    @campaign = Current.account.campaigns.create!(campaign_params)
  end

  def update
    @campaign.update!(campaign_params)
  end

  def destroy
    @campaign.destroy!
    head :ok
  end

  def preview_audience
    inbox = Current.account.inboxes.find(params.require(:inbox_id))
    audience = params.permit(audience: [:type, :id])[:audience]
    render json: Whatsapp::CampaignAudienceService.new(account: Current.account, inbox: inbox, audience: audience).summary
  end

  def test_message
    inbox = Current.account.inboxes.find(params.require(:inbox_id))
    template = Current.account.whatsapp_templates.find(params.require(:template_id))
    message_id = Whatsapp::CampaignTestSendService.new(
      inbox: inbox,
      template: template,
      phone_number: params.require(:phone_number),
      template_params: params.require(:template_params).permit!.to_h
    ).perform
    render json: { message_id: message_id }
  rescue Whatsapp::CampaignTestSendService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  def pause
    perform_campaign_control(:pause!)
  end

  def resume
    perform_campaign_control(:resume!)
  end

  def cancel
    perform_campaign_control(:cancel!)
  end

  private

  def campaign
    @campaign ||= Current.account.campaigns.find_by(display_id: params[:id])
  end

  def campaign_params
    permitted = params.require(:campaign).permit(:title, :description, :message, :enabled, :trigger_only_during_business_hours, :inbox_id, :sender_id,
                                                 :scheduled_at, audience: [:type, :id], trigger_rules: {}, template_params: {})
    template_name = permitted.dig(:template_params, :name)
    language = permitted.dig(:template_params, :language)
    if permitted[:inbox_id].present? && template_name.present? && language.present?
      template = Current.account.whatsapp_templates.find_by(inbox_id: permitted[:inbox_id], name: template_name, language: language)
      permitted[:whatsapp_template_id] = template.id if template.present?
    end
    permitted
  end

  def perform_campaign_control(action)
    Whatsapp::CampaignControlService.new(@campaign).public_send(action)
    render :show
  rescue ArgumentError => e
    render json: { error: e.message }, status: :unprocessable_entity
  end
end
