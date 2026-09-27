# Durian — Prawn building blocks for the ORM Monthly Report PDF (section
# headings, label/value tables, data tables, bar lists, number formatting).
#
# Prawn's built-in fonts only cover Western characters, so every string goes
# through #safe: an emoji or Indic script in a campaign / showroom name would
# otherwise abort the whole render.
module Reports::OrmMonthlyPdfLayout
  INK = '1F2933'.freeze
  MUTED = '6B7280'.freeze
  ACCENT = '0F766E'.freeze
  TILE = 'F3F4F6'.freeze
  RULE = 'E5E7EB'.freeze
  BAR_LABEL_WIDTH = 170

  private

  def section(pdf, title)
    pdf.start_new_page if pdf.cursor < 120
    pdf.move_down 10
    pdf.text title, size: 13, style: :bold, color: ACCENT
    pdf.stroke_color RULE
    pdf.stroke_horizontal_rule
    pdf.move_down 6
  end

  def key_values(pdf, pairs)
    table(pdf, nil, pairs, widths: { 0 => 330 })
  end

  def table(pdf, header, rows, widths: {})
    return if rows.empty?

    data = ([header].compact + rows).map { |row| row.map { |cell| cell_text(cell) } }
    pdf.table(data, width: pdf.bounds.width, header: !header.nil?,
                    cell_style: { size: 8, padding: [3, 5], borders: [:bottom], border_color: RULE, text_color: INK }) do |t|
      widths.each { |col, w| t.column(col).width = w }
      t.row(0).style(font_style: :bold, background_color: TILE) if header
    end
    pdf.move_down 8
  end

  # Horizontal bar list — compact and readable for small categorical counts.
  def bars(pdf, title, counts, max_rows: 8)
    return if counts.blank?

    pdf.start_new_page if pdf.cursor < 60
    pdf.text title, size: 9, style: :bold, color: INK
    pdf.move_down 4
    rows = counts.first(max_rows)
    scale = (pdf.bounds.width - BAR_LABEL_WIDTH - 50) / rows.map(&:last).max.to_f
    rows.each { |label, n| bar(pdf, label, n, [n * scale, 1].max) }
    pdf.move_down 6
  end

  def bar(pdf, label, count, length)
    pdf.start_new_page if pdf.cursor < 20
    y = pdf.cursor
    pdf.text_box safe(label), at: [0, y], width: BAR_LABEL_WIDTH - 8, height: 11, size: 8,
                              overflow: :shrink_to_fit, color: INK
    pdf.fill_color ACCENT
    pdf.fill_rectangle [BAR_LABEL_WIDTH, y - 1], length, 8
    pdf.fill_color INK
    pdf.text_box number(count), at: [BAR_LABEL_WIDTH + length + 4, y], width: 46, height: 11, size: 8
    pdf.move_down 12
  end

  def cell_text(cell)
    cell.is_a?(Numeric) ? number(cell) : safe(cell)
  end

  def duration(seconds)
    s = seconds.to_i
    return '-' if s.zero?
    return "#{s} sec" if s < 60
    return "#{(s / 60.0).round} min" if s < 3600
    return "#{s / 3600} h #{(s % 3600) / 60} min" if s < 86_400

    "#{(s / 86_400.0).round(1)} days"
  end

  def percent(part, whole)
    whole.to_i.zero? ? '-' : "#{(part * 100.0 / whole).round}%"
  end

  def number(value)
    ActiveSupport::NumberHelper.number_to_delimited(value.to_i)
  end

  def safe(value)
    value.to_s.encode('Windows-1252', invalid: :replace, undef: :replace, replace: '').encode('UTF-8').strip
  end
end
