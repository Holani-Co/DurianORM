<script setup>
// Multi-attachment bubble — used when a single message carries more than one
// attachment. Common producers:
//   • Gmail messages with several inline images / files
//   • Facebook carousel posts
//   • Instagram messages with multiple media items
//   • Bulk WhatsApp media uploads
//
// Pre-fix the Message.vue dispatcher only handled `attachments.length === 1`,
// so messages with 2+ attachments fell through to TextBubble and the user only
// saw small download chips — the actual media was invisible. This component
// stacks proper per-attachment previews and also renders the message caption
// at the top when present.

import { ref, computed } from 'vue';
import BaseBubble from './Base.vue';
import FormattedContent from './Text/FormattedContent.vue';
import Icon from 'next/icon/Icon.vue';
import { useSnakeCase } from 'dashboard/composables/useTransformKeys';
import { useMessageContext } from '../provider.js';
import { ATTACHMENT_TYPES } from '../constants';
import GalleryView from 'dashboard/components/widgets/conversation/components/GalleryView.vue';

const { attachments, content, filteredCurrentChatAttachments } =
  useMessageContext();

const hasCaption = computed(() => Boolean(content.value?.trim()));

const galleryAttachment = ref(null);
const openGallery = att => {
  galleryAttachment.value = useSnakeCase(att);
};
const closeGallery = () => {
  galleryAttachment.value = null;
};

const isImage = a => a.fileType === ATTACHMENT_TYPES.IMAGE;
const isVideo = a =>
  a.fileType === ATTACHMENT_TYPES.VIDEO ||
  a.fileType === ATTACHMENT_TYPES.IG_REEL;
const isAudio = a => a.fileType === ATTACHMENT_TYPES.AUDIO;

// Filename rendered for the generic file row. Backend doesn't always surface
// the original name on the attachment object; fall back to the URL tail so
// the agent at least sees something meaningful instead of a blank link.
const filenameFor = a => {
  if (a.fileName) return a.fileName;
  try {
    const url = new URL(a.dataUrl);
    const tail = url.pathname.split('/').pop();
    return decodeURIComponent(tail || a.fileType || 'file');
  } catch (e) {
    return a.fileType || 'file';
  }
};

// Image grid: 1-col when only one image among many, 2-col otherwise. Cheap
// approximation of WhatsApp/iMessage album layout — enough to be readable
// without pulling in a heavier gallery dep.
const imageGridClass = computed(() => {
  const imgCount = attachments.value.filter(isImage).length;
  return imgCount === 1 ? 'grid-cols-1' : 'grid-cols-2';
});
</script>

<template>
  <BaseBubble class="p-3" data-bubble-name="multi-attachment">
    <div v-if="hasCaption" class="mb-3">
      <FormattedContent :content="content" />
    </div>

    <div class="flex flex-col gap-3">
      <!-- Images: bunched into a grid so an N-photo message looks like one
           album rather than N stacked bubbles. -->
      <div
        v-if="attachments.some(isImage)"
        class="grid gap-2"
        :class="imageGridClass"
      >
        <button
          v-for="att in attachments.filter(isImage)"
          :key="att.id || att.dataUrl"
          type="button"
          class="overflow-hidden rounded-lg skip-context-menu focus:outline-none"
          @click.stop="openGallery(att)"
        >
          <img
            :src="att.thumbUrl || att.dataUrl"
            :alt="filenameFor(att)"
            class="object-cover w-full h-full"
            loading="lazy"
          />
        </button>
      </div>

      <!-- Videos + IG reels: inline player per item. -->
      <video
        v-for="att in attachments.filter(isVideo)"
        :key="att.id || att.dataUrl"
        controls
        class="max-w-full rounded-lg skip-context-menu"
        :src="att.dataUrl"
        @click.stop
      />

      <!-- Audio: native player so voice notes don't get hidden inside a chip. -->
      <audio
        v-for="att in attachments.filter(isAudio)"
        :key="att.id || att.dataUrl"
        controls
        class="w-full skip-context-menu"
        :src="att.dataUrl"
        @click.stop
      />

      <!-- Everything else (file, embed, unknown future types, etc.) — render a
           clear download row so the agent can at least access the artifact. -->
      <a
        v-for="att in attachments.filter(
          a => !isImage(a) && !isVideo(a) && !isAudio(a)
        )"
        :key="att.id || att.dataUrl"
        :href="att.dataUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="flex items-center gap-2 px-3 py-2 rounded-lg bg-n-alpha-1 hover:bg-n-alpha-2"
        @click.stop
      >
        <Icon icon="i-lucide-paperclip" class="text-n-slate-11" />
        <span class="truncate text-n-slate-12">{{ filenameFor(att) }}</span>
      </a>
    </div>
  </BaseBubble>

  <GalleryView
    v-if="galleryAttachment"
    :show="true"
    :attachment="galleryAttachment"
    :all-attachments="filteredCurrentChatAttachments"
    @close="closeGallery"
  />
</template>
