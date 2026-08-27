json.extract! campaign_delivery, :id, :campaign_id, :contact_id, :phone_number, :status, :skip_reason, :attempt_count,
              :meta_message_id, :error_code, :error_message, :queued_at, :sent_at, :delivered_at, :read_at, :replied_at, :failed_at, :created_at
json.contact do
  json.id campaign_delivery.contact.id
  json.name campaign_delivery.contact.name
end
