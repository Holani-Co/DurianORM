class Reports::GoogleReviewsCsv
  def initialize(account:, since:, till:, inbox: nil)
    @account = account
    @since = since
    @till = till
    @inbox = inbox
  end

  def generate
    CSV.generate do |csv|
      Reports::GoogleReviewStores::TYPES.each_with_index do |type, index|
        csv << [] if index.positive?
        csv << ["#{type} - Google Reviews Month of #{since.strftime('%B - %Y')}"]
        csv << ['Type', 'Showroom', 'Ratings', 'Reviews', 'Grand Total']
        Reports::GoogleReviewStores.for_type(type).each do |store|
          stat = stats[store[:label]]
          csv << [type, store[:name], stat&.average_rating, counts[store[:label]], stat&.total_review_count]
        end
      end
    end
  end

  private

  attr_reader :account, :since, :till, :inbox

  def conversations
    (inbox ? inbox.conversations : account.conversations)
      .where("additional_attributes ->> 'type' = 'google_review'")
  end

  def stats
    @stats ||= account.google_review_store_stats.index_by(&:store_label)
  end

  # Review dates come from Google. Parse in Ruby so one malformed legacy value
  # cannot abort the whole report through a database cast.
  def counts
    return @counts if defined?(@counts)

    @counts = Hash.new(0)
    conversations.find_each do |conversation|
      attrs = conversation.additional_attributes || {}
      posted_at = safe_time(attrs['review_created_at'])
      next if posted_at.nil? || posted_at.to_date < since || posted_at.to_date > till

      @counts[store_label(attrs['location'])] += 1
    end
    @counts
  end

  def store_label(location)
    slug = location.to_s.downcase.gsub(/[^a-z0-9]+/, '-').gsub(/\A-|-\z/, '')
    "store-#{slug}"
  end

  def safe_time(value)
    Time.zone.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end
end
