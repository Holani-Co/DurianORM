# Durian — HTML body of the Forwarded Emails weekly report email: the headline
# numbers, who received the emails, and the emails themselves (the attached
# Excel has every column). Email-safe inline styles; customer text is escaped.
class Reports::OrmForwardedEmailSummary
  INK = '#0F172A'.freeze
  MUTED = '#64748B'.freeze
  LINE = '#E2E8F0'.freeze
  CELL = "padding:6px 8px;font-size:13px;border-bottom:1px solid #{LINE};vertical-align:top;text-align:left;".freeze
  ROW_LIMIT = 50

  def initialize(data)
    @data = data
  end

  def html
    t = @data[:totals]
    tiles([['Emails forwarded', t[:forwarded]], ['Deals created', t[:deals]],
           ['Tickets raised', t[:tickets]], ['Resolved', t[:resolved]]]) +
      recipients(t[:by_recipient]) + emails(@data[:rows])
  end

  private

  def tiles(cells)
    inner = cells.map do |label, value|
      "<td style='padding:10px 14px;background:#F8FAFC;border:1px solid #{LINE};border-radius:8px;'>" \
        "<div style='font-size:22px;font-weight:700;color:#{INK};'>#{value}</div>" \
        "<div style='font-size:12px;color:#{MUTED};'>#{label}</div></td><td style='width:8px;'></td>"
    end.join
    "#{heading('At a glance')}<table role='presentation' cellpadding='0' cellspacing='0'><tr>#{inner}</tr></table>"
  end

  def recipients(by_recipient)
    return '' if by_recipient.blank?

    body = by_recipient.map do |email, count|
      "<tr><td style='#{CELL}color:#{MUTED};'>#{h(email)}</td>" \
        "<td style='#{CELL}color:#{INK};font-weight:600;text-align:right;'>#{count}</td></tr>"
    end.join
    "#{heading('Forwarded to')}<table role='presentation' cellpadding='0' cellspacing='0' " \
      "style='width:100%;max-width:420px;border-collapse:collapse;'>#{body}</table>"
  end

  def emails(rows)
    return "<p style='color:#{MUTED};font-size:14px;margin:18px 0;'>No emails in this category were forwarded this week.</p>" if rows.empty?

    head = %w[Date Customer Case Deal Status].map { |col| "<th style='#{CELL}color:#{MUTED};font-weight:600;'>#{col}</th>" }.join
    body = rows.first(ROW_LIMIT).map { |row| email_row(row) }.join
    more = rows.size - ROW_LIMIT
    note = more.positive? ? "<p style='color:#{MUTED};font-size:13px;margin:8px 0;'>+ #{more} more in the attached Excel.</p>" : ''
    "#{heading('The emails')}<table role='presentation' cellpadding='0' cellspacing='0' " \
      "style='width:100%;border-collapse:collapse;'><tr>#{head}</tr>#{body}</table>#{note}"
  end

  def email_row(row)
    customer = [row[:customer], row[:email]].compact_blank.map { |value| h(value) }.join('<br>')
    kase = "<strong>#{h(row[:subject].presence || '(no subject)')}</strong><br>" \
           "<span style='color:#{MUTED};'>#{h(row[:message])}</span>"
    deal = row[:deal] == 'Yes' ? h([row[:deal_no], row[:deal_stage]].compact_blank.join(' · ').presence || 'Yes') : '—'
    cells = [h(row[:forwarded_at]), customer, kase, deal, h(row[:status])]
    "<tr>#{cells.map { |cell| "<td style='#{CELL}color:#{INK};'>#{cell}</td>" }.join}</tr>"
  end

  def heading(text)
    "<h2 style='color:#{INK};font-size:16px;margin:22px 0 8px;'>#{text}</h2>"
  end

  def h(value)
    ERB::Util.html_escape(value.to_s)
  end
end
