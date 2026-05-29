#!/bin/bash
# Run from chatwoot/ directory
cd "/Users/adityasingh/Desktop/GHT/Durian ORM/chatwoot"
bundle exec rails runner "
account = Account.first
puts 'Account: ' + account.name.to_s

# Check if webhook already exists
existing = account.webhooks.find_by(url: 'http://localhost:8420/chatwoot/webhook')
if existing
  puts 'Webhook already exists: ' + existing.id.to_s
else
  wh = account.webhooks.create!(
    url: 'http://localhost:8420/chatwoot/webhook',
    subscriptions: ['conversation_status_changed']
  )
  puts 'Webhook created! ID: ' + wh.id.to_s + ', URL: ' + wh.url
end
"
