# Durian — Forwarded Emails weekly report Excel (attached to the report email):
# the daily report's single-sheet layout, one row per forwarded customer email.
class Reports::OrmForwardedXlsx < Reports::OrmDailyXlsx
  SHEET = 'Forwarded emails'.freeze
  HEADERS = ['Forwarded On', 'Conversation #', 'Customer Name', 'Email', 'Mobile', 'Subject', 'Message',
             'Subcategory', 'Forwarded To', 'Forwarded By', 'Deal Created', 'Deal No', 'Deal Stage',
             'Ticket No', 'Ticket Status', 'Status', 'Assigned Agent'].freeze
  WIDTHS = [18, 14, 24, 28, 16, 34, 60, 18, 30, 12, 12, 22, 18, 14, 16, 12, 20].freeze
  TEXT_COLUMNS = [4, 11, 13].freeze
  ROW_KEYS = %i[forwarded_at conversation_id customer email mobile subject message subcategory forwarded_to
                forwarded_by deal deal_no deal_stage ticket_no ticket_status status agent].freeze
end
