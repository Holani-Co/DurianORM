require 'rails_helper'

RSpec.describe DurianOs::ChangeReportJob do
  let(:url) { 'https://durianos.test/api/os/v2/changes' }
  let(:reporter_env) do
    { OS_REPORT_URL: url, OS_REPORT_KEY: 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8', OS_REPORT_KEY_ID: 'orm-2026-10' }
  end
  let(:body) { DurianOs::Reporter.build_body([{ kind: 'ping', at: '2026-10-29T10:00:00Z' }]) }

  it 'posts the body with a fresh HMAC signature' do
    stub = stub_request(:post, url).to_return(status: 202, body: '{"accepted":1}')
    with_modified_env reporter_env do
      travel_to Time.zone.at(1_793_181_720) do
        described_class.perform_now(body)
      end
      expected = DurianOs::Reporter.signature(body, 1_793_181_720)
      expect(stub.with(body: body, headers: { 'X-OS-App' => 'orm', 'X-OS-Key-Id' => 'orm-2026-10',
                                              'X-OS-Timestamp' => '1793181720', 'X-OS-Signature' => expected })).to have_been_made
    end
  end

  it 'signs each attempt afresh but keeps the same body' do
    stub_request(:post, url).to_return(status: 202)
    with_modified_env reporter_env do
      travel_to(Time.zone.at(1_793_181_720)) { described_class.perform_now(body) }
      travel_to(Time.zone.at(1_793_181_780)) { described_class.perform_now(body) }
    end
    timestamps = []
    expect(WebMock).to(have_requested(:post, url).with do |req|
      timestamps << req.headers['X-Os-Timestamp']
      req.body == body
    end.twice)
    expect(timestamps.uniq.size).to eq(2)
  end

  it 'schedules a retry when Durian OS refuses the report' do
    stub_request(:post, url).to_return(status: 401)
    with_modified_env reporter_env do
      expect { described_class.perform_now(body) }.to have_enqueued_job(described_class).with(body)
    end
  end

  it 'retries after 1, 5 and 15 minutes, then hourly for 24 hours' do
    expect(described_class::RETRY_WAITS.first(3)).to eq([1.minute, 5.minutes, 15.minutes])
    expect(described_class::RETRY_WAITS.drop(3)).to eq(Array.new(24, 1.hour))
  end

  it 'sends nothing when the reporter is not configured' do
    with_modified_env OS_REPORT_URL: nil, OS_REPORT_KEY: nil, OS_REPORT_KEY_ID: nil do
      described_class.perform_now(body)
    end
    expect(WebMock).not_to have_requested(:post, url)
  end
end
