# Ends every session of a user and rotates their API token (contract 4.12; ORM code changes 9 and 11).
module DurianOs::Sessions
  module_function

  def end_all!(user)
    sessions = user.tokens.is_a?(Hash) ? user.tokens.size : 0
    user.update!(tokens: {})
    token = user.access_token
    token&.regenerate_token
    { sessionsEnded: sessions, tokensRevoked: token ? 1 : 0 }
  end
end
