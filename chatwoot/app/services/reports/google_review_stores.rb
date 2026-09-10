module Reports::GoogleReviewStores
  TYPES = %w[FOFO COCO].freeze
  CONFIG_PATH = Rails.root.join('config/google_review_stores.yml')

  module_function

  def all
    @all ||= YAML.load_file(CONFIG_PATH).map do |label, meta|
      { label: label, type: meta['type'], name: meta['name'] }
    end
  end

  def for_type(type)
    all.select { |store| store[:type] == type }.sort_by { |store| store[:name] }
  end
end
