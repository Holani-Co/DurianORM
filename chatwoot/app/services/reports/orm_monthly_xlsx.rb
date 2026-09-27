# Durian — ORM Monthly Report as an Excel workbook, rendered from
# V2::Reports::OrmMonthlyReportBuilder#build: a Summary sheet (the same
# headline numbers as the PDF, with the previous month) plus row-level detail.
class Reports::OrmMonthlyXlsx
  HEADLINE_ROWS = Reports::OrmMonthlyPdf::HEADLINE_TILES
  # Detail-sheet columns: [title, width, stored as text?]. Text keeps phone
  # numbers / IDs from being turned into numbers (or scientific notation).
  FORWARD_COLUMNS = [['Date', 18], ['Customer', 28], ['Email', 32], ['Category', 30],
                     ['Subcategory', 20], ['How', 14]].freeze
  DEAL_COLUMNS = [['Date', 18], ['Customer', 26], ['Email', 30], ['Mobile', 16, true], ['Product line', 20],
                  ['Showroom', 26], ['Channel', 12], ['Created by', 24], ['Automatic', 10],
                  ['CRM Deal ID', 22, true]].freeze
  TICKET_COLUMNS = [['Date', 18], ['Ticket #', 12, true], ['Subject', 44], ['Status', 12], ['Source', 10],
                    ['Category', 28], ['Customer', 26], ['Channel', 12]].freeze

  def initialize(data)
    @data = data
  end

  def render
    package = Axlsx::Package.new
    workbook = package.workbook
    @bold = workbook.styles.add_style(b: true)
    @title = workbook.styles.add_style(b: true, sz: 14)
    @date = workbook.styles.add_style(format_code: 'dd-mmm-yyyy hh:mm')
    @percent = workbook.styles.add_style(num_fmt: 9) # 0%
    %i[summary emails forwarded deals tickets conversations_and_ai reviews campaigns].each do |sheet|
      send(sheet, workbook)
    end
    package.to_stream.read
  end

  private

  def summary(workbook)
    workbook.add_worksheet(name: 'Summary') do |sheet|
      sheet.add_row ["Durian ORM - Monthly Report - #{@data[:month_label]}"], style: @title
      sheet.add_row ["Generated #{@data[:generated_at].strftime('%d %b %Y, %I:%M %p')} IST. " \
                     'Each item is counted in the month it happened (India time).']
      sheet.add_row []
      sheet.add_row ['Metric', @data[:month_label], @data[:previous_label], 'Change'], style: @bold
      HEADLINE_ROWS.each { |key, label| sheet.add_row headline_row(key, label), style: [nil, nil, nil, @percent] }
      sheet.column_widths 40, 18, 18, 12
    end
  end

  def headline_row(key, label)
    now = @data[:headline][key].to_i
    before = @data[:previous][key].to_i
    [label, now, before, before.zero? ? nil : ((now - before).to_f / before).round(3)]
  end

  def emails(workbook)
    e = @data[:emails]
    workbook.add_worksheet(name: 'Emails') do |sheet|
      email_pairs(e).each { |row| sheet.add_row row }
      sheet.add_row []
      sheet.add_row ['Category', 'Emails', 'Auto', 'By agent', 'Forwarded', 'Subcategories'], style: @bold
      e[:categories].each do |c|
        sheet.add_row [c[:name], c[:total], c[:auto], c[:by_agent], c[:forwarded],
                       c[:subcategories].map { |name, n| "#{name}: #{n}" }.join(', ')]
      end
      sheet.column_widths 48, 10, 10, 10, 12, 50
    end
  end

  def email_pairs(emails)
    [['Emails received', emails[:received]],
     ['Category decided automatically by the AI', emails[:classified][:auto]],
     ['Category chosen by an agent', emails[:classified][:by_agent]],
     ['Still uncategorised', emails[:classified][:uncategorised]],
     *emails[:filtered].map { |intent, n| ["Filtered - #{intent}", n] },
     ['Forwarded automatically', emails[:forwarded][:auto]],
     ['Sent on by an agent', emails[:forwarded][:manual]],
     ['Waiting for a category decision (as of report time)', emails[:awaiting_review_now]]]
  end

  def forwarded(workbook)
    rows = @data[:emails][:forward_rows].map do |f|
      [f[:at], f[:customer], f[:email], f[:category], f[:subcategory], f[:automatic] ? 'Automatic' : 'By an agent']
    end
    detail(workbook, 'Forwarded emails', FORWARD_COLUMNS, rows)
  end

  def deals(workbook)
    rows = @data[:deals][:rows].map do |d|
      [*d.values_at(:at, :customer, :email, :mobile, :product_line, :showroom, :channel, :created_by),
       d[:automatic] ? 'Yes' : 'No', d[:crm_deal_id]]
    end
    detail(workbook, 'Deals', DEAL_COLUMNS, rows)
  end

  def tickets(workbook)
    rows = @data[:tickets][:rows].map { |t| t.values_at(:at, :number, :subject, :status, :source, :category, :customer, :channel) }
    detail(workbook, 'Tickets', TICKET_COLUMNS, rows)
  end

  def conversations_and_ai(workbook)
    s = @data[:social]
    workbook.add_worksheet(name: 'Conversations & AI') do |sheet|
      [['AI replies sent to customers', s[:ai_replies]],
       ['Conversations the AI replied in', s[:ai_conversations]],
       ['AI reply drafts prepared for the team', s[:drafts_prepared]],
       ['AI drafts approved and sent by an agent', s[:drafts_approved]],
       ['EMI enquiries', s[:emi_enquiries]],
       ['Average first response time (minutes)', (s[:avg_first_response_seconds] / 60.0).round(1)]]
        .each { |row| sheet.add_row row }
      sheet.add_row []
      sheet.add_row ['Channel', 'Conversations started'], style: @bold
      s[:by_channel].each { |channel, n| sheet.add_row [channel, n] }
      sheet.column_widths 44, 22
    end
  end

  def reviews(workbook)
    workbook.add_worksheet(name: 'Reviews') do |sheet|
      sheet.add_row ['Source', 'Reviews', 'Average rating', 'Replied', '5 star', '4 star', '3 star', '2 star',
                     '1 star'], style: @bold
      { 'Google' => @data[:reviews][:google], 'Website (durian.in)' => @data[:reviews][:website] }.each do |name, r|
        sheet.add_row [name, r[:count], r[:avg_stars], r[:replied], *5.downto(1).map { |s| r[:distribution][s] }]
      end
      sheet.column_widths 22, 10, 16, 10, 8, 8, 8, 8, 8
    end
  end

  def campaigns(workbook)
    c = @data[:campaigns]
    workbook.add_worksheet(name: 'WhatsApp campaigns') do |sheet|
      sheet.add_row %w[Campaign Attempted Sent Delivered Read Failed], style: @bold
      c[:by_campaign].each { |r| sheet.add_row r.values_at(:name, :attempted, :sent, :delivered, :read, :failed) }
      sheet.add_row ['Total', *c[:totals].values_at(:attempted, :sent, :delivered, :read, :failed)], style: @bold
      sheet.add_row []
      sheet.add_row ['Why messages failed', 'Meta error code', 'Messages'], style: @bold
      c[:failure_reasons].each { |f| sheet.add_row f.values_at(:reason, :code, :count), types: [nil, :string, nil] }
      sheet.column_widths 50, 16, 10, 12, 10, 10
    end
  end

  # Per-column cell types (text where flagged) and styles (first column = date).
  def column_formats(columns)
    [columns.map { |column| column[2] ? :string : nil }, [@date] + ([nil] * (columns.size - 1))]
  end

  # A row-level sheet (first column is a date) with filters on the header row.
  def detail(workbook, name, columns, rows)
    workbook.add_worksheet(name: name) do |sheet|
      sheet.add_row columns.map(&:first), style: @bold
      types, styles = column_formats(columns)
      rows.each { |row| sheet.add_row row, types: types, style: styles }
      sheet.auto_filter = "A1:#{Axlsx.col_ref(columns.size - 1)}#{rows.size + 1}" if rows.any?
      sheet.column_widths(*columns.map { |column| column[1] })
    end
  end
end
