class AddVerticalsToOffers < ActiveRecord::Migration[7.1]
  def up
    add_column :offers, :verticals, :jsonb, default: [], null: false
    # Existing offers are all furniture — tag them so the furniture sale offers
    # stop surfacing on the FHC / doors agent channels. An empty array still
    # means "all verticals" (a universal offer) going forward.
    execute(%(UPDATE offers SET verticals = '["furniture"]'::jsonb WHERE verticals = '[]'::jsonb))
  end

  def down
    remove_column :offers, :verticals
  end
end
