# Sends a ping report: when the worker starts and daily from config/schedule.yml (6.3; ORM code change 10).
class DurianOs::PingJob < ApplicationJob
  queue_as :scheduled_jobs

  def perform
    DurianOs::Reporter.ping
  end
end
