# How many AI requests run at once, and the replay guard (docs/mcp/MCP.md, section 10), in Redis (the $alfred
# pool), so both hold across Puma workers.
#
# - Slots: at most PER_PERSON requests at once for one person and DurianOs::Config.ai_concurrency in all. Each is
#   a key taken with SET NX EX 60 and given back when the request ends; 60 seconds outlive any request
#   (rack-timeout ends one at 15), so a slot heals if a process dies. Past either limit: 429, and the pass is
#   not spent.
# - The jti is spent once (SET NX EX 150): 150 seconds cover the whole window a pass can be used in (60 seconds of
#   life plus 30 of skew either side). Not set means used: 401 `replayed`.
# Redis unreachable fails closed: 503 `redis`.
class DurianOs::Ai::Slots
  PREFIX = 'orm_os_pass'.freeze
  PER_PERSON = 2
  SLOT_TTL = 60
  JTI_TTL = 150

  def self.take!(sub)
    new.take!(sub)
  end

  def self.spend!(jti, sub)
    fresh = redis { |conn| conn.set("#{PREFIX}:jti:#{jti}", sub, nx: true, ex: JTI_TTL) }
    DurianOs::Ai.refuse!(401, 'replayed', DurianOs::Ai::PASS_INVALID, headers: { 'www-authenticate' => 'Bearer' }) unless fresh
  end

  def self.redis(&)
    $alfred.with(&)
  rescue Redis::BaseError, ConnectionPool::TimeoutError, Errno::ECONNREFUSED
    DurianOs::Ai.refuse!(503, 'redis', DurianOs::Ai::UNAVAILABLE)
  end

  def initialize
    @token = SecureRandom.hex(8)
    @held = []
  end

  def take!(sub)
    take_one!((0...PER_PERSON).map { |n| "#{PREFIX}:slot:p:#{sub}:#{n}" }, 'person_busy')
    take_one!((0...DurianOs::Config.ai_concurrency).map { |n| "#{PREFIX}:slot:all:#{n}" }, 'busy')
    self
  rescue DurianOs::Ai::Refused
    release
    raise
  end

  # Gives back only the slots this request still holds (a key that expired and was taken again is another's).
  def release
    @held.each do |key|
      self.class.redis { |conn| conn.del(key) if conn.get(key) == @token }
    end
    @held.clear
  rescue DurianOs::Ai::Refused
    @held.clear
  end

  private

  def take_one!(keys, code)
    key = keys.find { |candidate| self.class.redis { |conn| conn.set(candidate, @token, nx: true, ex: SLOT_TTL) } }
    DurianOs::Ai.refuse!(429, code, DurianOs::Ai::BUSY, headers: { 'retry-after' => '2' }) unless key
    @held << key
  end
end
