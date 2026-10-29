require 'rails_helper'

RSpec.describe User do
  it 'allows one user per Durian OS person, and any number of unlinked users' do
    create(:user, custom_attributes: { 'os_person_id' => 'p-1' })
    create(:user)
    create(:user)

    expect { create(:user, custom_attributes: { 'os_person_id' => 'p-1' }) }.to raise_error(ActiveRecord::RecordNotUnique)
  end
end
