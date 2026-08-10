# Durian — Reviews dashboard.
#
# The Google-reviews pulse in-app (not just the CSV): how many came in, the
# average rating and its spread, how many were auto- vs manually-replied, the
# split by store location, and the recent low (1-2★) ratings that need a
# person. Reviews are bucketed by their ACTUAL posting date
# (additional_attributes.review_created_at), the same rule the CSV uses.
class V2::Reports::ReviewsBuilder
  include V2::Reports::OrmMetrics

  AUTO_LABEL = 'review-auto-replied'
  MANUAL_LABEL = 'review-manually-replied'
  LOW_STAR_MAX = 2
  LOW_LIST_LIMIT = 15

  def initialize(account:, params:)
    @account = account
    @params = params
  end

  def build
    rows = review_rows
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      summary: summary(rows),
      distribution: distribution(rows),
      by_location: by_location(rows),
      low_ratings: low_ratings(rows)
    }
  end

  private

  attr_reader :account, :params

  # One lightweight row per in-range review conversation.
  def review_rows
    convs = account.conversations.where("additional_attributes ->> 'type' = 'google_review'")
    convs.find_each.filter_map do |conv|
      attrs = conv.additional_attributes || {}
      posted = safe_time(attrs['review_created_at'])
      next if posted.nil? || !range.cover?(posted)

      labels = conv.cached_label_list_array
      {
        date: posted.to_date,
        stars: attrs['stars'].to_i,
        location: attrs['location'].to_s,
        name: (conv.contact&.name).to_s,
        auto: labels.include?(AUTO_LABEL),
        manual: labels.include?(MANUAL_LABEL)
      }
    end
  end

  def summary(rows)
    stars = rows.map { |r| r[:stars] }.select(&:positive?)
    replied = rows.count { |r| r[:auto] || r[:manual] }
    {
      count: rows.size,
      avg_stars: stars.empty? ? 0 : (stars.sum.to_f / stars.size).round(2),
      auto_reply_rate: replied.positive? ? (rows.count { |r| r[:auto] } * 100.0 / replied).round : 0,
      low_count: rows.count { |r| r[:stars].between?(1, LOW_STAR_MAX) }
    }
  end

  def distribution(rows)
    counts = rows.each_with_object(Hash.new(0)) { |r, h| h[r[:stars]] += 1 }
    (1..5).index_with { |s| counts[s] }
  end

  # Reviews per store location, richest first, with each location's average.
  def by_location(rows)
    rows.reject { |r| r[:location].blank? }
        .group_by { |r| r[:location] }
        .map do |location, group|
          stars = group.map { |r| r[:stars] }.select(&:positive?)
          {
            location: location,
            count: group.size,
            avg_stars: stars.empty? ? 0 : (stars.sum.to_f / stars.size).round(2)
          }
        end
        .sort_by { |h| -h[:count] }
        .first(12)
  end

  # Recent low (1-2★) reviews needing a person, newest first.
  def low_ratings(rows)
    rows.select { |r| r[:stars].between?(1, LOW_STAR_MAX) }
        .sort_by { |r| r[:date] }
        .reverse
        .first(LOW_LIST_LIMIT)
        .map { |r| { date: r[:date].to_s, stars: r[:stars], location: r[:location], name: r[:name] } }
  end

  def safe_time(value)
    return nil if value.blank?

    Time.zone.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end
end
