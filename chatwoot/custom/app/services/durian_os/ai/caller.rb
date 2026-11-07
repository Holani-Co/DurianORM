# The person an AI request acts as (docs/mcp/MCP.md, sections 3 and 4), read from ORM's own rows on every request.
#
# Found by users.custom_attributes->>'os_person_id' = sub (the unique index the Durian OS connector writes), never
# by email. Refused (401): no such user (`unknown_person`); a service or emergency user, an offboarded person, or
# no account_users row in the managed account (`no_account`).
#
# The ceiling is the pass's max_level: `administer`, `edit` (`act`, a personal token's word, is edit) or `view`
# (anything else). It only ever lowers: below administer an administrator acts as a plain agent
# (Custom::AccountUser). Every AI connection reads only in v1, whatever its ceiling.
class DurianOs::Ai::Caller
  CEILINGS = { 'view' => 'view', 'edit' => 'edit', 'act' => 'edit', 'administer' => 'administer' }.freeze
  MAX_CLIENT = 60

  attr_reader :user, :sub, :client, :ceiling, :ref

  def self.find!(claims)
    user = User.find_by("custom_attributes->>'os_person_id' = ?", claims['sub'])
    refuse!('unknown_person') if user.nil?
    refuse!('no_account') unless member?(user)

    new(user: user, sub: claims['sub'], client: client_of(claims['client']), ceiling: ceiling_of(claims['max_level']),
        ref: claims['jti'].to_s.first(6))
  end

  def self.member?(user)
    attributes = user.custom_attributes || {}
    return false if user.durian_os_protected? || attributes['os_offboarded_at'].present?

    AccountUser.exists?(account_id: DurianOs::Config.account_id, user_id: user.id)
  end

  def self.ceiling_of(max_level)
    max_level.is_a?(String) ? CEILINGS.fetch(max_level.strip.downcase, 'view') : 'view'
  end

  # The AI's name the pass may carry (at most 60 printable characters), else `AI`.
  def self.client_of(client)
    return 'AI' unless client.is_a?(String) && client.strip.present? && client.length <= MAX_CLIENT
    return 'AI' unless client.match?(/\A[[:print:]]+\z/)

    client.split.join(' ')
  end

  def self.refuse!(code)
    DurianOs::Ai.refuse!(401, code, DurianOs::Ai::PASS_INVALID, headers: { 'www-authenticate' => 'Bearer' })
  end

  def initialize(user:, sub:, client:, ceiling:, ref:)
    @user = user
    @sub = sub
    @client = client
    @ceiling = ceiling
    @ref = ref
  end

  def user_id
    user.id
  end

  # Only an `administer` pass keeps an administrator an administrator.
  def full_access?
    ceiling == 'administer'
  end

  def read_only?
    true
  end
end
