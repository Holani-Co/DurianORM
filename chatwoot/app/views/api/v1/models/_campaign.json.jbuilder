json.id resource.display_id
json.title resource.title
json.description resource.description
json.account_id resource.account_id
json.inbox do
  json.partial! 'api/v1/models/inbox', formats: [:json], resource: resource.inbox
end
json.sender do
  json.partial! 'api/v1/models/agent', formats: [:json], resource: resource.sender if resource.sender.present?
end
json.message resource.message
json.template_params resource.template_params
json.campaign_status resource.campaign_status
json.execution_status resource.execution_status
json.execution_started_at resource.execution_started_at&.to_i
json.execution_completed_at resource.execution_completed_at&.to_i
json.execution_error resource.execution_error
json.audience_snapshot_at resource.audience_snapshot_at&.to_i
json.whatsapp_template_id resource.whatsapp_template_id
json.audience_count resource.audience_count
json.eligible_count resource.eligible_count
json.skipped_count resource.skipped_count
json.sent_count resource.sent_count
json.delivered_count resource.delivered_count
json.read_count resource.read_count
json.failed_count resource.failed_count
json.enabled resource.enabled
json.campaign_type resource.campaign_type
if resource.campaign_type == 'one_off'
  json.scheduled_at resource.scheduled_at.to_i
  json.audience resource.audience
end
json.trigger_rules resource.trigger_rules
json.trigger_only_during_business_hours resource.trigger_only_during_business_hours
json.created_at resource.created_at
json.updated_at resource.updated_at
