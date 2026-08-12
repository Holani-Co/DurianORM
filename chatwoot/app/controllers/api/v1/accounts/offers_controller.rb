# Durian — CRUD for the client-managed promotional offers (ORM Offers tab).
# Admin-only. The image is uploaded as a multipart file and stored in
# ActiveStorage (has_one_attached :image on Offer). The bridge reads the live
# offers to surface one on a customer's greeting.
class Api::V1::Accounts::OffersController < Api::V1::Accounts::BaseController
  # Reading + sending are open to any agent (the bot fetches live offers for
  # greetings; agents send them in a conversation); managing is admin-only.
  before_action :check_admin, only: [:create, :update, :destroy]
  before_action :fetch_offer, only: [:update, :destroy, :send_to_conversation]

  def index
    @offers = Current.account.offers.order(priority: :asc, created_at: :desc)
  end

  def create
    @offer = Current.account.offers.new(offer_params)
    @offer.save!
    @offer.image.attach(params[:image]) if params[:image].present?
  end

  def update
    @offer.update!(offer_params)
    @offer.image.attach(params[:image]) if params[:image].present?
  end

  def destroy
    @offer.destroy!
    head :ok
  end

  # POST /offers/:id/send_to_conversation  { conversation_id }
  # Agent manually pushes this offer's image + caption to the customer. The image
  # and the caption go out as SEPARATE messages: a single Instagram message
  # carrying both is delivered as two sends but stores only one source_id, so the
  # attachment's echo isn't deduped and shows twice in the agent view. The
  # caption message also carries the offer link (if set) so the customer can tap
  # through.
  def send_to_conversation
    conversation = Current.account.conversations.find_by!(display_id: params[:conversation_id])
    return render json: { error: 'Offer has no image' }, status: :unprocessable_entity unless @offer.image.attached?

    image = conversation.messages.build(
      account_id: Current.account.id, inbox_id: conversation.inbox_id,
      message_type: :outgoing, sender: Current.user
    )
    image.attachments.build(account_id: Current.account.id, file_type: :image)
         .file.attach(@offer.image.blob)
    image.save!

    caption = [@offer.caption, @offer.link].compact_blank.join("\n")
    if caption.present?
      conversation.messages.create!(
        account_id: Current.account.id, inbox_id: conversation.inbox_id,
        message_type: :outgoing, content: caption, sender: Current.user
      )
    end
    render json: { success: true }
  end

  private

  def fetch_offer
    @offer = Current.account.offers.find(params[:id])
  end

  def offer_params
    params.permit(:caption, :priority, :active, :expires_at, :link, tags: [])
  end

  def check_admin
    return if Current.account_user&.administrator?

    render json: { error: 'Admin access required' }, status: :unauthorized
  end
end
