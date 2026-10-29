# With OS_SIGNIN_ONLY on, confirming an email no longer signs the user in; the
# answer points at the Durian OS hand-off (ORM appendix code change 6; F5).
module Custom::DeviseOverrides::ConfirmationsController
  def create
    return super unless DurianOs::Config.signin_only?

    confirmable = User.find_by(confirmation_token: params[:confirmation_token])
    return render_confirmation_error_for(confirmable) unless confirmable&.confirm

    render json: { message: 'Confirmed', redirect_url: DurianOs::Config.handoff_url }
  end

  private

  def render_confirmation_error_for(confirmable)
    @confirmable = confirmable
    render_confirmation_error
  end
end
