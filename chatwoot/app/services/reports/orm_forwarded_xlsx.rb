# Durian — Forwarded Emails weekly report Excel (attached to the report email):
# one row per forwarded customer email, with who received it and the outcome.
class Reports::OrmForwardedXlsx
  HEADERS = ['Forwarded On', 'Conversation #', 'Customer Name', 'Email', 'Mobile', 'Subject', 'Message',
             'Subcategory', 'Forwarded To', 'Forwarded By', 'Deal Created', 'Deal No', 'Deal Stage',
             'Ticket No', 'Ticket Status', 'Status', 'Assigned Agent'].freeze
  WIDTHS = [18, 14, 24, 28, 16, 34, 60, 18, 30, 12, 12, 22, 18, 14, 16, 12, 20].freeze
  # Columns kept as text so long ids/numbers don't render in scientific notation.
  TEXT_COLUMNS = [4, 11, 13].freeze
  # Row hash keys in the same order as HEADERS.
  ROW_KEYS = %i[forwarded_at conversation_id customer email mobile subject message subcategory forwarded_to
                forwarded_by deal deal_no deal_stage ticket_no ticket_status status agent].freeze

  def initialize(data)
    @data = data
  end

  def render
    package = Axlsx::Package.new
    workbook = package.workbook
    header = workbook.styles.add_style(b: true, fg_color: 'FFFFFF', bg_color: '1F3864',
                                       alignment: { wrap_text: true, vertical: :center })
    workbook.add_worksheet(name: 'Forwarded emails') { |sheet| fill(sheet, header) }
    package.to_stream.read
  end

  private

  def fill(sheet, header)
    sheet.add_row HEADERS, style: header
    types = HEADERS.each_index.map { |i| TEXT_COLUMNS.include?(i) ? :string : nil }
    @data[:rows].each { |row| sheet.add_row row.values_at(*ROW_KEYS), types: types }
    sheet.column_widths(*WIDTHS)
    sheet.auto_filter = "A1:#{Axlsx.col_ref(HEADERS.size - 1)}#{@data[:rows].size + 1}" if @data[:rows].any?
    sheet.sheet_view.pane do |pane|
      pane.state = :frozen
      pane.y_split = 1
      pane.active_pane = :bottom_left
    end
  end
end
