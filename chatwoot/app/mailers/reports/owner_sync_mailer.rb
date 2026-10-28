class Reports::OwnerSyncMailer < ApplicationMailer
  def alert(recipients, report)
    return unless smtp_config_set_or_development?
    return if recipients.blank?

    @problems = Array(report['problems']).map { |p| row(p) }
    @synced_at = report['synced_at'].to_s
    send_mail_with_liquid(to: recipients,
                          subject: "Durian ORM - #{@problems.size} deal owner(s) inactive in Zoho")
  end

  private

  def row(problem)
    successor = problem['successor'] || {}
    {
      'where' => problem['kind'] == 'store' ? "Store: #{problem['label']}" : "Routing: #{problem['group']} · #{problem['label']}",
      'owner' => [problem['owner_name'], problem['owner_email']].compact_blank.join(' · ').presence || problem['owner_id'],
      'status' => problem['status'],
      'successor' => successor.present? ? "#{successor['name']} (#{successor['email']}) — #{successor['reason']}" : 'none — please pick one'
    }
  end

  def liquid_locals
    super.merge(problems: @problems, synced_at: @synced_at)
  end
end
