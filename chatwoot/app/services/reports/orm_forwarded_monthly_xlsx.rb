# Durian — Forwarded Emails MONTHLY overall report Excel (all categories for the
# month, attached to Shilpi's monthly email). Same single-sheet layout as the
# weekly per-category sheet, plus a Category column so every forwarded email
# across all categories is in one place.
class Reports::OrmForwardedMonthlyXlsx < Reports::OrmForwardedXlsx
  SHEET = 'Forwarded emails'.freeze
  HEADERS = ['Forwarded On', 'Conversation #', 'Category', 'Customer Name', 'Email', 'Mobile', 'Subject',
             'Message', 'Subcategory', 'Forwarded To', 'Forwarded By', 'Deal Created', 'Deal No', 'Deal Stage',
             'Ticket No', 'Ticket Status', 'Status', 'Assigned Agent'].freeze
  WIDTHS = [18, 14, 24, 24, 28, 16, 34, 60, 18, 30, 12, 12, 22, 18, 14, 16, 12, 20].freeze
  TEXT_COLUMNS = [5, 12, 14].freeze
  ROW_KEYS = %i[forwarded_at conversation_id category customer email mobile subject message subcategory forwarded_to
                forwarded_by deal deal_no deal_stage ticket_no ticket_status status agent].freeze
end
