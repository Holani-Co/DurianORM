# One-time backfill (Rails side): write additional_attributes.review_created_at
# onto existing Google Reviews conversations, joining on the review_path stored
# in custom_attributes. Reads the JSON map produced by
# zoho-bridge/backfill_review_dates.py.
#
#   bundle exec rails runner scripts/backfill_review_dates.rb <review_dates.json>
#
# Idempotent: conversations that already have a review_created_at are skipped,
# so it's safe to re-run after the poller adds more dates.

path = ARGV[0] || abort('usage: rails runner scripts/backfill_review_dates.rb <review_dates.json>')
map = JSON.parse(File.read(path)) # { "accounts/.../reviews/<id>" => "2026-06-09T..." }

inbox = Inbox.find_by!(name: 'Google Reviews')
updated = already_had = no_match = 0

Conversation.where(inbox_id: inbox.id).find_each do |conv|
  add = conv.additional_attributes || {}
  if add['review_created_at'].present?
    already_had += 1
    next
  end

  review_path = (conv.custom_attributes || {})['review_path']
  create_time = review_path && map[review_path]
  unless create_time
    no_match += 1
    next
  end

  conv.update!(additional_attributes: add.merge('review_created_at' => create_time))
  updated += 1
end

puts "updated=#{updated} already_had=#{already_had} no_google_match=#{no_match}"
