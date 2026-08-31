json.payload do
  json.array! @campaign_deliveries, partial: 'api/v1/models/campaign_delivery', as: :campaign_delivery
end

json.meta do
  json.current_page @campaign_deliveries.current_page
  json.total_pages @campaign_deliveries.total_pages
  json.total_count @campaign_deliveries.total_count
end
