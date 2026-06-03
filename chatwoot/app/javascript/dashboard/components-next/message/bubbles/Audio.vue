<script setup>
import { computed } from 'vue';
import BaseBubble from './Base.vue';
import AudioChip from 'next/message/chips/Audio.vue';
import FormattedContent from './Text/FormattedContent.vue';
import { useMessageContext } from '../provider.js';

const { attachments, content } = useMessageContext();

const attachment = computed(() => {
  return attachments.value[0];
});
// Captions can ride alongside voice notes / forwarded audio. With the
// dispatcher now picking AudioBubble even when content exists, render
// the text right under the player.
const hasCaption = computed(() => Boolean(content.value?.trim()));
</script>

<template>
  <BaseBubble class="bg-transparent" data-bubble-name="audio">
    <AudioChip
      :attachment="attachment"
      class="p-2 text-n-slate-12 skip-context-menu"
    />
    <div v-if="hasCaption" class="mt-2 px-2 pb-1">
      <FormattedContent :content="content" />
    </div>
  </BaseBubble>
</template>
