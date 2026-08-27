json.extract! whatsapp_template, :id, :inbox_id, :meta_template_id, :name, :language, :category, :status, :components,
              :rejection_reason, :quality_rating, :submitted_at, :approved_at, :rejected_at, :last_synced_at, :created_at, :updated_at
json.inbox do
  json.id whatsapp_template.inbox.id
  json.name whatsapp_template.inbox.name
end
