# Durian — ORM Daily Report Excel (attached to the daily email), in the client's
# requested layout: a single sheet, one row per conversation, matching the
# "Chatwoot Report Pointer" template columns exactly.
class Reports::OrmDailyXlsx
  HEADERS = ['Date', 'Chatwoot id', 'Conversation Source', 'Channel', '1st response date and Time',
             'Assignment & Handling Type', 'Tagged', 'Auto Classified', 'Assigned Email id',
             'Deal Creation Method', 'Deal Id', 'Customer Name', 'Mobile', 'Email', 'City', 'Subject',
             'Category', 'Subcategory', 'Product Line', 'Showroom / Owner', 'Status', 'Assigned Agent',
             'Team', 'Priority', 'Ticket No', 'Ticket Status', 'Deal Created By', 'Deal URL'].freeze
  WIDTHS = [20, 12, 18, 20, 24, 24, 16, 26, 26, 20, 16, 24, 16, 26, 16, 30, 20, 20, 18, 22, 12, 20,
            18, 12, 14, 16, 22, 40].freeze
  # Columns kept as text so long ids/numbers don't render in scientific notation.
  TEXT_COLUMNS = [10, 12, 24].freeze
  # Row hash keys in the same order as HEADERS.
  ROW_KEYS = %i[date chatwoot_id source channel first_response handling tagged auto_classified
                assigned_email deal_method deal_id customer mobile email city subject category
                subcategory product_line showroom status agent team priority ticket_no ticket_status
                deal_created_by deal_url].freeze

  def initialize(data)
    @data = data
  end

  def render
    package = Axlsx::Package.new
    workbook = package.workbook
    header = workbook.styles.add_style(b: true, fg_color: 'FFFFFF', bg_color: '1F3864',
                                       alignment: { wrap_text: true, vertical: :center })
    workbook.add_worksheet(name: 'Report') { |sheet| fill(sheet, header) }
    package.to_stream.read
  end

  private

  def fill(sheet, header)
    sheet.add_row HEADERS, style: header
    types = HEADERS.each_index.map { |i| TEXT_COLUMNS.include?(i) ? :string : nil }
    @data[:rows].each { |row| sheet.add_row row_values(row), types: types }
    sheet.column_widths(*WIDTHS)
    sheet.auto_filter = "A1:#{Axlsx.col_ref(HEADERS.size - 1)}#{@data[:rows].size + 1}" if @data[:rows].any?
    freeze_header(sheet)
  end

  def freeze_header(sheet)
    sheet.sheet_view.pane do |pane|
      pane.state = :frozen
      pane.y_split = 1
      pane.active_pane = :bottom_left
    end
  end

  def row_values(row)
    row.values_at(*ROW_KEYS)
  end
end
