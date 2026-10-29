#!/usr/bin/env ruby
# frozen_string_literal: true

# A standalone stdlib script, not Rails code.
# rubocop:disable Rails/TimeZone, Rails/Output, Rails/Exit, Metrics/AbcSize, Metrics/CyclomaticComplexity
# rubocop:disable Metrics/MethodLength, Metrics/PerceivedComplexity, Metrics/ClassLength

# Durian OS conformance for ORM, run against a LOCAL server only (contract 9.2, ORM appendix 10.8).
#
# ORM serves no /os/v2: Durian OS reaches it through its connector. So, of 9.2, the checks
# that apply here are:
#   - "ORM serves no /os/v2": requests signed with a throwaway Ed25519 key get 404, never 2xx/401.
#   - check 9, ping part: a ping report arrives, signed as in 3.6 (this script is the receiver).
#   - check 8 (clock), informational.
# plus the app-side changes the connector relies on (10.8): the Platform durian_os endpoints,
# the one-time login link, and the OS_SIGNIN_ONLY switch.
#
# Usage (stdlib only):
#   ORM_BASE_URL=http://127.0.0.1:3107 \
#   ORM_PLATFORM_TOKEN=... ORM_TEST_USER_ID=... \          # optional: Platform checks
#   OS_REPORT_KEY=... OS_REPORT_KEY_ID=... RECEIVER_PORT=3199 PING_WAIT=60 \   # optional: ping check
#   EXPECT_SIGNIN_ONLY=1 ORM_EMERGENCY_EMAIL=emergency@orm.test \               # optional
#   ruby script/durian_os_conformance.rb
# script/durian_os_conformance_seed.rb prepares a local database and writes these values to
# tmp/durian_os_conformance.env. Never point this at production.

require 'base64'
require 'digest'
require 'json'
require 'net/http'
require 'openssl'
require 'securerandom'
require 'socket'
require 'time'
require 'uri'

class DurianOsConformance
  APP = 'orm'
  EMPTY_SHA = Base64.urlsafe_encode64(Digest::SHA256.digest(''), padding: false)

  def initialize(env)
    @env = env
    @base = URI(env.fetch('ORM_BASE_URL', 'http://127.0.0.1:3107'))
    abort 'Refusing to run: ORM_BASE_URL must be a local address.' unless %w[127.0.0.1 localhost ::1].include?(@base.host)
    @key = OpenSSL::PKey.generate_key('ED25519') # throwaway, never stored
    @results = []
    @skipped = []
  end

  def run
    pings = start_receiver
    check_no_os_v2
    check_clock
    check_platform_and_handoff if @env['ORM_PLATFORM_TOKEN'].to_s != ''
    @env['EXPECT_SIGNIN_ONLY'] == '1' ? check_signin_only : check_signin_today
    check_ping(pings) if pings
    report
  end

  private

  # --- 9.2: ORM serves no /os/v2 -------------------------------------------------------

  def check_no_os_v2
    [
      ['GET', '/os/v2/description', ''],
      ['GET', '/os/v2/people?limit=50', ''],
      ['GET', '/os/v2/people/os-conformance-probe', ''],
      ['PUT', '/os/v2/people/os-conformance-probe/access', '{"requestId":"c0nf0rm4","expectedVersion":"x","values":{}}'],
      ['GET', '/api/os/v2/description', '']
    ].each do |method, path, body|
      res = http(method, path, body: body, headers: { 'X-OS-Request-Token' => request_token(method, path, body) })
      record("no /os/v2: signed #{method} #{path} is 404", res.code == '404', "got #{res.code}")
    end
  end

  def request_token(method, path, body)
    now = Time.now.to_i
    header = { alg: 'EdDSA', kid: 'conformance-throwaway' }
    claims = {
      iss: 'https://durianos.in', aud: APP, typ: 'os_request', iat: now, exp: now + 60,
      jti: Base64.urlsafe_encode64(SecureRandom.random_bytes(16), padding: false),
      method: method, path: path.delete_prefix('/api'),
      bodySha256: body.empty? ? EMPTY_SHA : Base64.urlsafe_encode64(Digest::SHA256.digest(body), padding: false),
      actor: { kind: 'system', job: 'conformance' }
    }
    signing_input = [header, claims].map { |part| Base64.urlsafe_encode64(part.to_json, padding: false) }.join('.')
    "#{signing_input}.#{Base64.urlsafe_encode64(@key.sign(nil, signing_input), padding: false)}"
  end

  # --- 9.2 check 8 (informational for ORM) ----------------------------------------------

  def check_clock
    date = http('GET', '/health')['Date']
    # Puma sends no Date header; ORM's front proxy adds it. 9.2 check 8 does not apply to ORM.
    return @skipped << 'clock: no Date header from Puma (check 8 does not apply to ORM)' unless date

    skew = (Time.httpdate(date) - Time.now).abs
    record('clock: Date header within 30 s', skew <= 30, "skew #{skew.round(1)} s")
  end

  # --- 10.8: Platform durian_os endpoints and the one-time login link -------------------

  def check_platform_and_handoff
    token = { 'api_access_token' => @env['ORM_PLATFORM_TOKEN'] }
    record('status needs the Platform token', http('GET', '/platform/api/v1/durian_os/status').code == '401')
    res = http('GET', '/platform/api/v1/durian_os/status', headers: token)
    enabled = parse(res).dig('emergencyLogin', 'enabled')
    record('status answers emergencyLogin.enabled', res.code == '200' && [true, false].include?(enabled), "#{res.code} #{res.body}")
    expected = @env['EXPECT_EMERGENCY_ON'] == '1'
    record("emergency login reported #{expected ? 'on' : 'off'}", enabled == expected)

    user_id = @env['ORM_TEST_USER_ID']
    return record('one-time login link (needs ORM_TEST_USER_ID)', false, 'not set') if user_id.to_s == ''

    link = URI(parse(http('GET', "/platform/api/v1/users/#{user_id}/login", headers: token))['url'].to_s)
    record('login link points at ORM itself', link.host == @base.host, link.host.to_s)
    params = URI.decode_www_form(link.query.to_s).to_h
    sso = { email: params['email'], sso_auth_token: params['sso_auth_token'] }.to_json
    json = { 'Content-Type' => 'application/json' }

    page = http('GET', "/app/login?#{link.query}")
    record('login page with the token: Cache-Control no-store', page['Cache-Control'].to_s.include?('no-store'), page['Cache-Control'])
    record('login page with the token: Referrer-Policy no-referrer', page['Referrer-Policy'] == 'no-referrer', page['Referrer-Policy'])

    first = http('POST', '/auth/sign_in', body: sso, headers: json)
    record('one-time token signs in once', first.code == '200' && first['access-token'], "got #{first.code}")
    second = http('POST', '/auth/sign_in', body: sso, headers: json)
    record('the same token a second time is refused', second.code == '401', "got #{second.code}")
    return unless first.code == '200'

    session = { 'access-token' => first['access-token'], 'client' => first['client'], 'uid' => first['uid'] }
    record('session is valid', http('GET', '/auth/validate_token', headers: session).code == '200')
    ended = http('POST', "/platform/api/v1/durian_os/users/#{user_id}/end_sessions", headers: token)
    counts = parse(ended)
    record('end_sessions returns the counts', ended.code == '200' && counts['sessionsEnded'].to_i >= 1 && counts['tokensRevoked'] == 1,
           "#{ended.code} #{ended.body}")
    record('the session is gone after end_sessions', http('GET', '/auth/validate_token', headers: session).code == '401')
  end

  # --- 10.8: the OS_SIGNIN_ONLY switch ---------------------------------------------------

  def check_signin_only
    json = { 'Content-Type' => 'application/json' }
    password = { email: "probe-#{SecureRandom.hex(4)}@orm.test", password: 'Wrong@123456' }.to_json
    record('password sign-in is 404', http('POST', '/auth/sign_in', body: password, headers: json).code == '404')
    record('password reset is 404', http('POST', '/auth/password', body: { email: 'probe@orm.test' }.to_json, headers: json).code == '404')
    record('registration is 404', http('POST', '/auth', body: password, headers: json).code == '404')
    console = http('GET', '/super_admin/sign_in').code
    if @env['EXPECT_EMERGENCY_ON'] == '1'
      record('super admin console opens while the emergency login is on', console == '200', "got #{console}")
    else
      record('super admin console is 404', console == '404', "got #{console}")
    end
    login = http('GET', '/app/login')
    record('/app/login redirects to Durian OS', login.code == '302' && login['Location'].to_s.include?('/api/handoff/orm'), login['Location'])
    return if @env['ORM_EMERGENCY_EMAIL'].to_s == ''

    emergency = { email: @env['ORM_EMERGENCY_EMAIL'], password: 'x' * 24 }.to_json
    code = http('POST', '/auth/sign_in', body: emergency, headers: json).code
    if @env['EXPECT_EMERGENCY_ON'] == '1'
      record('emergency login refuses a wrong password (401)', code == '401', "got #{code}")
    else
      record('emergency login is 404 while off', code == '404', "got #{code}")
    end
  end

  def check_signin_today
    json = { 'Content-Type' => 'application/json' }
    password = { email: "probe-#{SecureRandom.hex(4)}@orm.test", password: 'Wrong@123456' }.to_json
    code = http('POST', '/auth/sign_in', body: password, headers: json).code
    record('switch unset: password sign-in still answers (401 for bad credentials)', code == '401', "got #{code}")
  end

  # --- 9.2 check 9, ping part: this script is the Durian OS receiver --------------------

  def start_receiver
    port = @env['RECEIVER_PORT'].to_s
    return nil if port == '' || @env['OS_REPORT_KEY'].to_s == ''

    raw = @env['OS_REPORT_KEY']
    @report_key = Base64.urlsafe_decode64(raw + ('=' * ((4 - (raw.length % 4)) % 4)))
    server = TCPServer.new(@env.fetch('RECEIVER_BIND', '0.0.0.0'), port.to_i)
    pings = Queue.new
    Thread.new { loop { handle_report(server.accept, pings) } }
    pings
  end

  def handle_report(socket, pings)
    request_line = socket.gets.to_s
    headers = {}
    while (line = socket.gets) && line != "\r\n"
      name, value = line.split(':', 2)
      headers[name.downcase] = value.to_s.strip
    end
    body = socket.read(headers['content-length'].to_i).to_s
    verdict = verify_report(request_line, headers, body)
    status = verdict[:ok] ? '202 Accepted' : '401 Unauthorized'
    socket.write("HTTP/1.1 #{status}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
    pings << verdict if verdict[:ping] || !verdict[:ok]
  ensure
    socket.close
  end

  def verify_report(request_line, headers, body)
    message = "v1:#{APP}:#{headers['x-os-key-id']}:#{headers['x-os-timestamp']}:#{Digest::SHA256.hexdigest(body)}"
    expected = "v1=#{OpenSSL::HMAC.hexdigest('SHA256', @report_key, message)}"
    payload = JSON.parse(body)
    problems = []
    problems << 'path' unless request_line.start_with?('POST /api/os/v2/changes ')
    problems << 'X-OS-App' unless headers['x-os-app'] == APP && payload['app'] == APP
    problems << 'key id' unless headers['x-os-key-id'] == @env['OS_REPORT_KEY_ID']
    problems << 'signature' unless OpenSSL.secure_compare(expected, headers['x-os-signature'].to_s)
    problems << 'timestamp' unless (Time.now.to_i - headers['x-os-timestamp'].to_i).abs <= 300
    problems << 'reportId' unless payload['reportId'].to_s.match?(/\A\h{8}-\h{4}-4\h{3}-[89ab]\h{3}-\h{12}\z/)
    { ok: problems.empty?, ping: payload['changes'].to_a.any? { |c| c['kind'] == 'ping' }, problems: problems }
  rescue JSON::ParserError
    { ok: false, ping: false, problems: ['body'] }
  end

  def check_ping(pings)
    wait = @env.fetch('PING_WAIT', '60').to_i
    verdict = nil
    deadline = Time.now + wait
    verdict = pings.pop(timeout: [deadline - Time.now, 0].max) while Time.now < deadline && !verdict&.dig(:ping)
    record('ping report arrives, signed as in 3.6', verdict && verdict[:ok] && verdict[:ping],
           verdict ? "problems: #{verdict[:problems].join(', ')}" : "no ping within #{wait} s")
  end

  # --- plumbing --------------------------------------------------------------------------

  def http(method, path, body: '', headers: {})
    uri = URI.join(@base.to_s, path)
    request = Net::HTTP.const_get(method.capitalize).new(uri.request_uri, headers)
    request.body = body unless body.empty?
    Net::HTTP.start(uri.host, uri.port, open_timeout: 5, read_timeout: 30) { |h| h.request(request) }
  end

  def parse(res)
    JSON.parse(res.body.to_s)
  rescue JSON::ParserError
    {}
  end

  def record(name, passed, detail = nil)
    @results << [name, passed ? true : false, detail]
  end

  def report
    @results.each { |name, ok, detail| puts "#{ok ? 'PASS' : 'FAIL'}  #{name}#{ok || detail.nil? ? '' : "  (#{detail})"}" }
    @skipped.each { |name| puts "SKIP  #{name}" }
    failed = @results.count { |_, ok, _| !ok }
    puts "\n#{@results.size - failed}/#{@results.size} checks passed"
    exit(failed.zero? ? 0 : 1)
  end
end

DurianOsConformance.new(ENV).run if $PROGRAM_NAME == __FILE__
# rubocop:enable Rails/TimeZone, Rails/Output, Rails/Exit, Metrics/AbcSize, Metrics/CyclomaticComplexity
# rubocop:enable Metrics/MethodLength, Metrics/PerceivedComplexity, Metrics/ClassLength
