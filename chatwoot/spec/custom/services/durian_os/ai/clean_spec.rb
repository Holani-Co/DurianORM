require 'rails_helper'

# What an AI answer may carry (docs/mcp/MCP.md, section 5). The whole stack is in spec/custom/requests.
RSpec.describe DurianOs::Ai::Clean do
  let(:note) { described_class::FILE_NOTE }
  let(:cdn) { 'https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1&signature=Signed' }

  it 'replaces any link under a file key, whatever its host, and keeps the file details' do
    attachment = { 'id' => 7, 'file_type' => 'image', 'extension' => 'jpg', 'file_size' => 2048,
                   'data_url' => 'https://media.example.test/a.jpg', 'thumb_url' => 'https://media.example.test/t.jpg',
                   'external_url' => cdn, 'download_url' => '/somewhere/a.jpg' }
    expect(described_class.json('payload' => [{ 'attachments' => [attachment] }])).to eq(
      'payload' => [{ 'attachments' => [attachment.merge('data_url' => note, 'thumb_url' => note, 'external_url' => note,
                                                         'download_url' => note)] }]
    )
  end

  it 'replaces a story, an ad and an avatar link, keeps a blank one and a location\'s coordinates' do
    value = {
      'content_attributes' => { 'story_url' => cdn, 'referral' => { 'image_url' => cdn, 'video_url' => cdn, 'thumbnail_url' => cdn,
                                                                    'source_url' => 'https://durian.in/sofas' } },
      'sender' => { 'thumbnail' => 'https://avatars.example.test/1.png', 'avatar_url' => '' },
      'location' => { 'file_type' => 'location', 'coordinates_lat' => 12.97, 'coordinates_long' => 77.59,
                      'data_url' => 'https://maps.google.com/?q=12.97,77.59', 'thumb_url' => '' },
      'images' => { 'media_url' => [cdn, 'https://media.example.test/b.jpg'] }
    }
    expect(described_class.json(value)).to eq(
      'content_attributes' => { 'story_url' => note, 'referral' => { 'image_url' => note, 'video_url' => note, 'thumbnail_url' => note,
                                                                     'source_url' => 'https://durian.in/sofas' } },
      'sender' => { 'thumbnail' => note, 'avatar_url' => '' },
      'location' => { 'file_type' => 'location', 'coordinates_lat' => 12.97, 'coordinates_long' => 77.59, 'data_url' => note, 'thumb_url' => '' },
      'images' => { 'media_url' => [note, note] }
    )
  end

  it 'replaces a signed, ORM or provider CDN link under any key, and keeps other text' do
    links = ['https://bucket.test/a?X-Amz-Signature=abc', 'https://storage.test/a?X-Goog-Signature=abc', '/rails/active_storage/blobs/x/a.png',
             'see https://scontent.xx.fbcdn.net/v/a.jpg?oh=1', 'https://scontent.cdninstagram.com/v/a.jpg']
    expect(described_class.json('content' => links)).to eq('content' => [note] * links.size)
    expect(described_class.json('content' => 'Price list: https://durian.in/sofas', 'count' => 3, 'ok' => true, 'none' => nil))
      .to eq('content' => 'Price list: https://durian.in/sofas', 'count' => 3, 'ok' => true, 'none' => nil)
  end

  it 'removes secrets and signed ids at any depth' do
    value = { 'id' => 1, 'media_signed_id' => 'eyJfcmFpbHMi--abc', 'media_filename' => 'promo.jpg',
              'inbox' => { 'name' => 'Sales', 'Secret' => 's', 'provider_config' => { 'api_key' => 'k' } },
              'blobs' => [{ 'signed_id' => 'eyJfcmFpbHMi--def', 'filename' => 'a.pdf' }] }
    expect(described_class.json(value)).to eq('id' => 1, 'media_filename' => 'promo.jpg', 'inbox' => { 'name' => 'Sales' },
                                              'blobs' => [{ 'filename' => 'a.pdf' }])
  end

  it 'drops session and cookie headers, whatever their case' do
    headers = { 'Content-Type' => 'application/json', 'access-token' => 't', 'Client' => 'c', 'UID' => 'u', 'set-cookie' => 'x=1' }
    expect(described_class.headers(headers)).to eq('Content-Type' => 'application/json')
    expect(described_class.header(headers, 'content-type')).to eq('application/json')
  end
end
