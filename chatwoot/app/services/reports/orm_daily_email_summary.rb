# Durian — short HTML overview in the body of the daily ORM email (the full
# per-conversation detail is in the attached Excel). Email-safe inline styles.
class Reports::OrmDailyEmailSummary
  INK = '#0F172A'.freeze
  MUTED = '#64748B'.freeze
  LINE = '#E2E8F0'.freeze

  def initialize(data)
    @data = data
  end

  def html
    t = @data[:totals]
    tiles([['Conversations', t[:conversations]], ['Needed an agent', t[:agent_needed]],
           ['Forwarded', t[:forwarded]], ['Deals created', t[:deals]]]) + by_source(t[:by_source])
  end

  private

  def tiles(cells)
    inner = cells.map do |label, value|
      "<td style='padding:10px 14px;background:#F8FAFC;border:1px solid #{LINE};border-radius:8px;'>" \
        "<div style='font-size:22px;font-weight:700;color:#{INK};'>#{value}</div>" \
        "<div style='font-size:12px;color:#{MUTED};'>#{label}</div></td><td style='width:8px;'></td>"
    end.join
    "<h2 style='color:#{INK};font-size:16px;margin:18px 0 10px;'>At a glance</h2>" \
      "<table role='presentation' cellpadding='0' cellspacing='0'><tr>#{inner}</tr></table>"
  end

  def by_source(by_source)
    return '' if by_source.blank?

    body = by_source.map do |source, count|
      "<tr><td style='padding:4px 0;color:#{MUTED};font-size:13px;border-bottom:1px solid #{LINE};'>#{source}</td>" \
        "<td style='padding:4px 0;color:#{INK};font-size:13px;font-weight:600;text-align:right;" \
        "border-bottom:1px solid #{LINE};'>#{count}</td></tr>"
    end.join
    "<h2 style='color:#{INK};font-size:16px;margin:22px 0 6px;'>By channel</h2>" \
      "<table role='presentation' cellpadding='0' cellspacing='0' style='width:100%;max-width:360px;'>#{body}</table>"
  end
end
