<script setup>
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMessageFormatter } from 'shared/composables/useMessageFormatter';
import { getInboxIconByType } from 'dashboard/helper/inbox';

import CardLayout from 'dashboard/components-next/CardLayout.vue';
import Button from 'dashboard/components-next/button/Button.vue';
import LiveChatCampaignDetails from './LiveChatCampaignDetails.vue';
import SMSCampaignDetails from './SMSCampaignDetails.vue';

const props = defineProps({
  title: {
    type: String,
    default: '',
  },
  message: {
    type: String,
    default: '',
  },
  isLiveChatType: {
    type: Boolean,
    default: false,
  },
  isEnabled: {
    type: Boolean,
    default: false,
  },
  status: {
    type: String,
    default: '',
  },
  sender: {
    type: Object,
    default: null,
  },
  inbox: {
    type: Object,
    default: null,
  },
  scheduledAt: {
    type: Number,
    default: 0,
  },
  executionStatus: { type: String, default: '' },
  audienceCount: { type: Number, default: 0 },
  sentCount: { type: Number, default: 0 },
  deliveredCount: { type: Number, default: 0 },
  readCount: { type: Number, default: 0 },
  failedCount: { type: Number, default: 0 },
});

const emit = defineEmits([
  'edit',
  'delete',
  'pause',
  'resume',
  'cancel',
  'viewDeliveries',
]);

const { t } = useI18n();

const STATUS_COMPLETED = 'completed';

const { formatMessage } = useMessageFormatter();

const isActive = computed(() =>
  props.isLiveChatType ? props.isEnabled : props.status !== STATUS_COMPLETED
);

const statusTextColor = computed(() => ({
  'text-n-teal-11': isActive.value,
  'text-n-slate-12': !isActive.value,
}));

const isWhatsapp = computed(
  () => props.inbox?.channel_type === 'Channel::Whatsapp'
);

const executionLabels = computed(() => ({
  draft: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.DRAFT'),
  scheduled: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.SCHEDULED'),
  queued: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.QUEUED'),
  running: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.RUNNING'),
  paused: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.PAUSED'),
  completed: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.COMPLETED'),
  cancelled: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.CANCELLED'),
  failed: t('CAMPAIGN.WHATSAPP.CARD.EXECUTION.FAILED'),
}));

const campaignStatus = computed(() => {
  if (props.isLiveChatType) {
    return props.isEnabled
      ? t('CAMPAIGN.LIVE_CHAT.CARD.STATUS.ENABLED')
      : t('CAMPAIGN.LIVE_CHAT.CARD.STATUS.DISABLED');
  }

  if (isWhatsapp.value && props.executionStatus) {
    return executionLabels.value[props.executionStatus];
  }

  return props.status === STATUS_COMPLETED
    ? t('CAMPAIGN.SMS.CARD.STATUS.COMPLETED')
    : t('CAMPAIGN.SMS.CARD.STATUS.SCHEDULED');
});

const canPause = computed(
  () =>
    isWhatsapp.value &&
    ['scheduled', 'queued', 'running'].includes(props.executionStatus)
);
const canResume = computed(
  () => isWhatsapp.value && ['paused', 'failed'].includes(props.executionStatus)
);
const canCancel = computed(
  () =>
    isWhatsapp.value &&
    !['completed', 'cancelled'].includes(props.executionStatus)
);

const inboxName = computed(() => props.inbox?.name || '');

const inboxIcon = computed(() => {
  const { medium, channel_type: type } = props.inbox;
  return getInboxIconByType(type, medium);
});
</script>

<template>
  <CardLayout layout="row">
    <div class="flex flex-col items-start justify-between flex-1 min-w-0 gap-2">
      <div class="flex justify-between gap-3 w-fit">
        <span
          class="text-base font-medium capitalize text-n-slate-12 line-clamp-1"
        >
          {{ title }}
        </span>
        <span
          class="text-xs font-medium inline-flex items-center h-6 px-2 py-0.5 rounded-md bg-n-alpha-2"
          :class="statusTextColor"
        >
          {{ campaignStatus }}
        </span>
      </div>
      <div
        v-dompurify-html="formatMessage(message, false, false, false)"
        class="text-sm text-n-slate-11 line-clamp-1 [&>p]:mb-0 h-6"
      />
      <div class="flex items-center w-full h-6 gap-2 overflow-hidden">
        <LiveChatCampaignDetails
          v-if="isLiveChatType"
          :sender="sender"
          :inbox-name="inboxName"
          :inbox-icon="inboxIcon"
        />
        <SMSCampaignDetails
          v-else
          :inbox-name="inboxName"
          :inbox-icon="inboxIcon"
          :scheduled-at="scheduledAt"
        />
      </div>
      <p v-if="isWhatsapp && audienceCount" class="text-xs text-n-slate-10">
        {{
          t('CAMPAIGN.WHATSAPP.CARD.METRICS', {
            audience: audienceCount,
            sent: sentCount,
            delivered: deliveredCount,
            read: readCount,
            failed: failedCount,
          })
        }}
      </p>
    </div>
    <div class="flex items-center justify-end gap-2">
      <Button
        v-if="isWhatsapp && audienceCount"
        variant="faded"
        size="sm"
        color="slate"
        icon="i-lucide-list-checks"
        @click="emit('viewDeliveries')"
      />
      <Button
        v-if="canPause"
        variant="faded"
        size="sm"
        color="amber"
        icon="i-lucide-pause"
        @click="emit('pause')"
      />
      <Button
        v-if="canResume"
        variant="faded"
        size="sm"
        color="teal"
        icon="i-lucide-play"
        @click="emit('resume')"
      />
      <Button
        v-if="canCancel"
        variant="faded"
        size="sm"
        color="slate"
        icon="i-lucide-ban"
        @click="emit('cancel')"
      />
      <Button
        v-if="isLiveChatType"
        variant="faded"
        size="sm"
        color="slate"
        icon="i-lucide-sliders-vertical"
        @click="emit('edit')"
      />
      <Button
        variant="faded"
        color="ruby"
        size="sm"
        icon="i-lucide-trash"
        @click="emit('delete')"
      />
    </div>
  </CardLayout>
</template>
