class Api::V1::Accounts::WhatsappTemplatesController < Api::V1::Accounts::BaseController
  before_action :check_authorization
  before_action :set_template, only: [:show, :update, :destroy, :submit]
  before_action :set_inbox, only: [:create, :sync, :upload_sample]

  def index
    @whatsapp_templates = Current.account.whatsapp_templates.includes(:inbox).latest_first
    @whatsapp_templates = @whatsapp_templates.where(inbox_id: params[:inbox_id]) if params[:inbox_id].present?
  end

  def show; end

  def create
    @whatsapp_template = Current.account.whatsapp_templates.create!(
      template_params.merge(inbox: @inbox, submitted_by: Current.user)
    )
  end

  def update
    @whatsapp_template.assign_attributes(template_params.except(:inbox_id))
    if @whatsapp_template.status == 'DRAFT'
      @whatsapp_template.save!
    else
      management_service(@whatsapp_template.inbox).update!(@whatsapp_template)
    end
  rescue Whatsapp::TemplateManagementService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  def destroy
    management_service(@whatsapp_template.inbox).delete!(@whatsapp_template)
    head :ok
  rescue Whatsapp::TemplateManagementService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  def submit
    @whatsapp_template.submitted_by = Current.user
    management_service(@whatsapp_template.inbox).submit!(@whatsapp_template)
    render :show
  rescue Whatsapp::TemplateManagementService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  def sync
    @whatsapp_templates = management_service(@inbox).sync!
    render :index
  rescue Whatsapp::TemplateManagementService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  def upload_sample
    handle = management_service(@inbox).upload_sample!(params.require(:file))
    render json: { handle: handle }
  rescue Whatsapp::TemplateManagementService::Error => e
    render json: { error: e.message }, status: :unprocessable_entity
  end

  private

  def set_template
    @whatsapp_template = Current.account.whatsapp_templates.find(params[:id])
  end

  def set_inbox
    inbox_id = params[:inbox_id] || params.dig(:whatsapp_template, :inbox_id)
    @inbox = Current.account.inboxes.find(inbox_id)
    management_service(@inbox)
  end

  def management_service(inbox)
    Whatsapp::TemplateManagementService.new(inbox)
  end

  def template_params
    permitted = params.require(:whatsapp_template).permit(:name, :language, :category)
    components = params.require(:whatsapp_template)[:components]
    permitted[:components] = components.map { |component| component.permit!.to_h } if components.respond_to?(:map)
    permitted
  end
end
