json.extract! whatsapp_consent, :id, :inbox_id, :contact_id, :purpose, :status, :source, :source_reference, :recorded_at, :details, :created_at
json.contact do
  json.id whatsapp_consent.contact.id
  json.name whatsapp_consent.contact.name
  json.phone_number whatsapp_consent.contact.phone_number
end
json.inbox do
  json.id whatsapp_consent.inbox.id
  json.name whatsapp_consent.inbox.name
end
