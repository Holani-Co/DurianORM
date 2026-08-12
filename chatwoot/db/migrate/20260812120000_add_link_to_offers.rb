# Durian — optional click-through URL for an offer. When set, it's appended to
# the offer's caption on send so the customer can tap through to the offer page.
class AddLinkToOffers < ActiveRecord::Migration[7.1]
  def change
    add_column :offers, :link, :string
  end
end
