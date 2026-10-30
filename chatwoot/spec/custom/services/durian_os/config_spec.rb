require 'rails_helper'

RSpec.describe DurianOs::Config do
  let(:hash12) { BCrypt::Password.create('emergency-password-123456789', cost: 12).to_s }
  let(:emergency_env) do
    { OS_EMERGENCY_EMAIL: 'emergency@orm.test', OS_EMERGENCY_PASSWORD_HASH: hash12 }
  end

  describe 'switches' do
    it 'keeps every switch off when unset' do
      with_modified_env OS_SIGNIN_ONLY: nil, OS_MANAGED: nil, OS_EMERGENCY_LOGIN_UNTIL: nil, OS_REPORT_KEY: nil do
        expect(described_class.signin_only?).to be(false)
        expect(described_class.managed?).to be(false)
        expect(described_class.emergency_login_on?).to be(false)
        expect(described_class.reporter_enabled?).to be(false)
      end
    end

    it 'keeps View as off unless OS_VIEW_AS is on' do
      with_modified_env(OS_VIEW_AS: nil) { expect(described_class.view_as?).to be(false) }
      with_modified_env(OS_VIEW_AS: 'off') { expect(described_class.view_as?).to be(false) }
      with_modified_env(OS_VIEW_AS: 'on') { expect(described_class.view_as?).to be(true) }
    end

    it 'turns OS_SIGNIN_ONLY on only for on-like values' do
      with_modified_env(OS_SIGNIN_ONLY: 'on') { expect(described_class.signin_only?).to be(true) }
      with_modified_env(OS_SIGNIN_ONLY: 'off') { expect(described_class.signin_only?).to be(false) }
    end
  end

  describe '.emergency_login_on?' do
    it 'is off by default, even with the email and hash configured' do
      with_modified_env(**emergency_env, OS_EMERGENCY_LOGIN_UNTIL: nil) do
        expect(described_class.emergency_login_on?).to be(false)
      end
    end

    it 'is on for a time in the future, at most 24 hours ahead' do
      with_modified_env(**emergency_env, OS_EMERGENCY_LOGIN_UNTIL: 2.hours.from_now.utc.iso8601) do
        expect(described_class.emergency_login_on?).to be(true)
      end
    end

    it 'is off for a past time' do
      with_modified_env(**emergency_env, OS_EMERGENCY_LOGIN_UNTIL: 1.minute.ago.utc.iso8601) do
        expect(described_class.emergency_login_on?).to be(false)
      end
    end

    it 'is off for a time more than 24 hours ahead' do
      with_modified_env(**emergency_env, OS_EMERGENCY_LOGIN_UNTIL: 25.hours.from_now.utc.iso8601) do
        expect(described_class.emergency_login_on?).to be(false)
      end
    end

    it 'is off for an unreadable time' do
      with_modified_env(**emergency_env, OS_EMERGENCY_LOGIN_UNTIL: 'tomorrow') do
        expect(described_class.emergency_login_on?).to be(false)
      end
    end

    it 'is off when the hash is bcrypt with a cost below 12' do
      weak = BCrypt::Password.create('emergency-password-123456789', cost: 4).to_s
      with_modified_env(**emergency_env, OS_EMERGENCY_PASSWORD_HASH: weak, OS_EMERGENCY_LOGIN_UNTIL: 1.hour.from_now.utc.iso8601) do
        expect(described_class.emergency_login_on?).to be(false)
      end
    end
  end

  describe '.report_key' do
    it 'decodes a 32-byte base64url key, padding restored' do
      with_modified_env OS_REPORT_KEY: 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8' do
        expect(described_class.report_key.bytes).to eq((0..31).to_a)
      end
    end

    it 'refuses a key that is not 32 bytes, so the app does not start' do
      with_modified_env OS_REPORT_KEY: Base64.urlsafe_encode64('short', padding: false) do
        expect { described_class.validate! }.to raise_error(DurianOs::Config::InvalidReportKey)
      end
    end
  end
end
