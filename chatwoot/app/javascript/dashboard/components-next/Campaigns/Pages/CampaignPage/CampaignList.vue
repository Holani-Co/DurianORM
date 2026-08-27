<script setup>
import CampaignCard from 'dashboard/components-next/Campaigns/CampaignCard/CampaignCard.vue';

defineProps({
  campaigns: {
    type: Array,
    required: true,
  },
  isLiveChatType: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits([
  'edit',
  'delete',
  'pause',
  'resume',
  'cancel',
  'viewDeliveries',
]);

const handleEdit = campaign => emit('edit', campaign);
const handleDelete = campaign => emit('delete', campaign);
const handlePause = campaign => emit('pause', campaign);
const handleResume = campaign => emit('resume', campaign);
const handleCancel = campaign => emit('cancel', campaign);
const handleViewDeliveries = campaign => emit('viewDeliveries', campaign);
</script>

<template>
  <div class="flex flex-col gap-4">
    <CampaignCard
      v-for="campaign in campaigns"
      :key="campaign.id"
      :title="campaign.title"
      :message="campaign.message"
      :is-enabled="campaign.enabled"
      :status="campaign.campaign_status"
      :sender="campaign.sender"
      :inbox="campaign.inbox"
      :scheduled-at="campaign.scheduled_at"
      :execution-status="campaign.execution_status"
      :audience-count="campaign.audience_count"
      :sent-count="campaign.sent_count"
      :delivered-count="campaign.delivered_count"
      :read-count="campaign.read_count"
      :failed-count="campaign.failed_count"
      :is-live-chat-type="isLiveChatType"
      @edit="handleEdit(campaign)"
      @delete="handleDelete(campaign)"
      @pause="handlePause(campaign)"
      @resume="handleResume(campaign)"
      @cancel="handleCancel(campaign)"
      @view-deliveries="handleViewDeliveries(campaign)"
    />
  </div>
</template>
