class Reports::GoogleReviewsCsv
  STATUS_LABELS = {
    'review-auto-replied' => 'Auto-replied',
    'review-manually-replied' => 'Manually replied',
    'review-replied' => 'Replied'
  }.freeze

  def initialize(account:, since:, till:, inbox: nil)
    @account = account
    @since = since
    @till = till
    @inbox = inbox
  end

  def generate
    CSV.generate do |csv|
      csv << ['Durian - Google Reviews Report']
      csv << ['Period', "#{since} to #{till}"]
      csv << []
      summary_rows.each { |row| csv << row }
      csv << []
      csv << ['Date', 'Stars', 'Store', 'Reviewer', 'Review', 'Reply status', 'Replied by']
      rows.each do |row|
        csv << [row[:date], row[:stars].zero? ? '' : row[:stars], row[:location], row[:reviewer],
                row[:comment], row[:status], row[:agent]]
      end
    end
  end

  private

  attr_reader :account, :since, :till, :inbox

  def conversations
    (inbox ? inbox.conversations : account.conversations)
      .where("additional_attributes ->> 'type' = 'google_review'")
  end

  # Review dates are supplied by Google. Parse in Ruby so one malformed legacy
  # value cannot abort the complete report through a database cast.
  def rows
    return @rows if defined?(@rows)

    unsorted_rows = conversations.filter_map { |conversation| row_for(conversation) }
    @rows = unsorted_rows.sort_by { |row| row[:date] }.reverse
  end

  def row_for(conversation)
    attrs = conversation.additional_attributes || {}
    posted_at = safe_time(attrs['review_created_at'])
    return if posted_at.nil? || posted_at.to_date < since || posted_at.to_date > till

    review_row(conversation, attrs, posted_at)
  end

  def review_row(conversation, attrs, posted_at)
    labels = conversation.cached_label_list_array
    {
      date: posted_at.to_date,
      stars: attrs['stars'].to_i,
      location: attrs['location'].to_s,
      reviewer: attrs['reviewer'].presence || conversation.contact&.name.to_s,
      comment: attrs['review_comment'].to_s,
      status: reply_status(labels),
      agent: replied_by(labels)
    }
  end

  def safe_time(value)
    Time.zone.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end

  def reply_status(labels)
    STATUS_LABELS.each { |label, status| return status if labels.include?(label) }
    'Unreplied'
  end

  def replied_by(labels)
    slug = labels.find { |label| label.start_with?('replied-by-') }
    slug&.delete_prefix('replied-by-')&.tr('-', ' ')&.titleize.to_s
  end

  def summary_rows
    rated = rows.reject { |row| row[:stars].zero? }
    average = rated.any? ? (rated.sum { |row| row[:stars] }.to_f / rated.size).round(2) : '-'

    [%w[Rating Count]] +
      5.downto(1).map { |stars| ["#{stars} star", rows.count { |row| row[:stars] == stars }] } +
      [['Unrated', rows.count { |row| row[:stars].zero? }],
       ['Total reviews', rows.size],
       ['Average rating', average]]
  end
end
