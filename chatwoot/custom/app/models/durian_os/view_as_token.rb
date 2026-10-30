# Prepended to User: DeviseTokenAuth defines create_token on the class itself, so an
# included module cannot wrap it. A View as session token (users.tokens entry with
# "os_view_as") never lives past imp_exp, and a renewal of the same client keeps the
# admin and the end (shared View as spec 2).
module DurianOs::ViewAsToken
  def create_token(client: nil, lifespan: nil, cost: nil, **token_extras)
    data = token_extras[:os_view_as] || (client && DurianOs::ViewAs.token_data(tokens.is_a?(Hash) ? tokens[client] : nil))
    session = DurianOs::ViewAs::Session.from_h(data)
    if session
      token_extras[:os_view_as] = session.to_record
      lifespan = DurianOs::ViewAs.lifespan(session, lifespan)
    end

    super(client: client, lifespan: lifespan, cost: cost, **token_extras)
  end
end
