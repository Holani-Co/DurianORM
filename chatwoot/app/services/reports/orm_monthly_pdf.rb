# Durian — ORM Monthly Report as a PDF for management, rendered from
# V2::Reports::OrmMonthlyReportBuilder#build. Aggregates only (no customer
# details) — the Excel carries the row-level detail.
class Reports::OrmMonthlyPdf
  include Reports::OrmMonthlyPdfLayout
  # Every string is reduced to Western characters (#safe), so Prawn's
  # "limited international text support" warning is just log noise here.
  Prawn::Fonts::AFM.hide_m17n_warning = true

  HEADLINE_TILES = [
    [:conversations, 'Conversations handled'], [:emails, 'Emails received'],
    [:auto_classified, 'Emails auto-classified by AI'], [:forwarded, 'Emails forwarded to teams'],
    [:deals, 'CRM deals created'], [:tickets, 'Zoho Desk tickets raised'],
    [:ai_replies, 'AI replies sent'], [:reviews, 'Reviews received'],
    [:campaign_delivered, 'Campaign messages delivered']
  ].freeze
  TILE_GAP = 8
  TILE_HEIGHT = 60

  def initialize(data)
    @data = data
  end

  def render
    pdf = Prawn::Document.new(page_size: 'A4', margin: [36, 36, 44, 36],
                              info: { Title: safe("Durian ORM report - #{@data[:month_label]}") })
    pdf.font 'Helvetica'
    %i[header headline emails deals_and_tickets conversations_and_ai reviews campaigns footer].each do |part|
      send(part, pdf)
    end
    pdf.render
  end

  private

  def header(pdf)
    pdf.text 'Durian ORM - Monthly Report', size: 20, style: :bold, color: INK
    pdf.text safe(@data[:month_label]), size: 13, color: ACCENT
    pdf.text "Generated #{@data[:generated_at].strftime('%d %b %Y, %I:%M %p')} IST", size: 8, color: MUTED
    pdf.move_down 14
  end

  # 3×3 grid of headline numbers, each with the change vs the previous month.
  def headline(pdf)
    width = (pdf.bounds.width - (2 * TILE_GAP)) / 3
    top = pdf.cursor
    HEADLINE_TILES.each_with_index do |(key, label), i|
      tile(pdf, [(i % 3) * (width + TILE_GAP), top - ((i / 3) * (TILE_HEIGHT + TILE_GAP))], width, key, label)
    end
    pdf.move_cursor_to top - (3 * (TILE_HEIGHT + TILE_GAP)) - 6
  end

  def tile(pdf, origin, width, key, label)
    x, y = origin
    pdf.fill_color TILE
    pdf.fill_rounded_rectangle [x, y], width, TILE_HEIGHT, 6
    pdf.fill_color INK
    pdf.bounding_box([x + 10, y - 8], width: width - 20, height: TILE_HEIGHT - 12) do
      pdf.text number(@data[:headline][key]), size: 18, style: :bold, color: INK
      pdf.text label, size: 8, color: INK
      pdf.text change(key), size: 7, color: MUTED
    end
  end

  def emails(pdf)
    e = @data[:emails]
    section(pdf, 'Emails')
    key_values(pdf, [
                 ['Emails received', e[:received]],
                 ['Category decided automatically by the AI', e[:classified][:auto]],
                 ['Category chosen by an agent (AI was unsure)', e[:classified][:by_agent]],
                 ['Still uncategorised', e[:classified][:uncategorised]],
                 ['Filtered out (promotional / automated / spam)', e[:filtered].values.sum],
                 ['Forwarded to the right team automatically', e[:forwarded][:auto]],
                 ['Sent on by an agent', e[:forwarded][:manual]],
                 ['Waiting for a category decision right now', e[:awaiting_review_now]]
               ])
    table(pdf, ['Category', 'Emails', 'Auto', 'By agent', 'Forwarded', 'Top subcategories'],
          e[:categories].first(12).map { |c| category_cells(c) }, widths: { 0 => 150, 5 => 170 })
  end

  def category_cells(category)
    top = category[:subcategories].first(3).map { |name, n| "#{name} (#{n})" }.join(', ')
    [category[:name], category[:total], category[:auto], category[:by_agent], category[:forwarded], top]
  end

  def deals_and_tickets(pdf)
    d = @data[:deals]
    t = @data[:tickets]
    section(pdf, 'Deals & tickets')
    key_values(pdf, [['CRM deals created', d[:total]], ['...of which created automatically by the ORM', d[:automatic]],
                     ['Zoho Desk tickets raised', t[:total]], ['...of which raised automatically', t[:automatic]]])
    bars(pdf, 'Deals by product line', d[:by_product_line])
    bars(pdf, 'Deals by channel', d[:by_channel])
    bars(pdf, 'Top showrooms (retail deals)', d[:by_showroom])
    bars(pdf, 'Tickets by category', t[:by_category])
  end

  def conversations_and_ai(pdf)
    s = @data[:social]
    section(pdf, 'Conversations & AI')
    key_values(pdf, [
                 ['AI replies sent to customers', s[:ai_replies]],
                 ['Conversations the AI replied in', s[:ai_conversations]],
                 ['AI reply drafts prepared for the team', s[:drafts_prepared]],
                 ['...approved and sent by an agent', s[:drafts_approved]],
                 ['EMI enquiries', s[:emi_enquiries]],
                 ['Average first response time', duration(s[:avg_first_response_seconds])]
               ])
    bars(pdf, 'Conversations by channel', s[:by_channel])
  end

  def reviews(pdf)
    section(pdf, 'Reviews')
    rows = { 'Google' => @data[:reviews][:google], 'Website (durian.in)' => @data[:reviews][:website] }
           .map { |name, review| review_cells(name, review) }
    table(pdf, ['Source', 'Reviews', 'Avg rating', 'Replied', '5 star', '4 star', '3 star', '2 star', '1 star'], rows)
  end

  def review_cells(name, review)
    rating = review[:avg_stars].positive? ? format('%.2f', review[:avg_stars]) : '-'
    [name, review[:count], rating, "#{review[:replied]} (#{percent(review[:replied], review[:count])})",
     *5.downto(1).map { |star| review[:distribution][star] }]
  end

  def campaigns(pdf)
    c = @data[:campaigns]
    total = c[:totals]
    section(pdf, 'WhatsApp campaigns')
    shares = %i[delivered read failed].map do |k|
      [k.to_s.capitalize, "#{number(total[k])} (#{percent(total[k], total[:attempted])})"]
    end
    key_values(pdf, [['Messages sent', total[:sent]]] + shares)
    table(pdf, %w[Campaign Attempted Delivered Read Failed],
          c[:by_campaign].first(10).map { |r| r.values_at(:name, :attempted, :delivered, :read, :failed) },
          widths: { 0 => 220 })
    table(pdf, ['Why messages failed', 'Messages'], c[:failure_reasons].first(6).map { |f| f.values_at(:reason, :count) },
          widths: { 0 => 360 })
  end

  def footer(pdf)
    pdf.move_down 8
    pdf.text 'How to read this report: dates are India time, and each item is counted in the month it ' \
             'happened (a deal in the month it was created, a ticket in the month it was raised). ' \
             "The on-screen ORM Overview counts by the conversation's start date, so it can differ slightly.",
             size: 7, color: MUTED
    pdf.number_pages safe("Durian ORM - #{@data[:month_label]}   |   Page <page> of <total>"),
                     at: [0, -14], width: pdf.bounds.width, align: :right, size: 7, color: MUTED
  end

  def change(key)
    now = @data[:headline][key].to_i
    before = @data[:previous][key].to_i
    month = @data[:previous_label].to_s.split.first
    return "No activity in #{month} either" if now.zero? && before.zero?
    return "New - none in #{month}" if before.zero?

    pct = ((now - before) * 100.0 / before).round
    return "Same as #{month}" if pct.zero?

    "#{pct.positive? ? 'Up' : 'Down'} #{pct.abs}% vs #{month} (#{number(before)})"
  end
end
