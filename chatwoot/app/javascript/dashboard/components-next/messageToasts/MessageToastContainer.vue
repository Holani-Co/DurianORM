<script setup>
// Slide-in toast notifications for new incoming messages. Listens to the
// `messageToasts` Vuex module (populated by helper/actionCable.js#onMessageCreated)
// and renders a small stack at the bottom-right. Click a toast → navigates to
// that conversation. Auto-dismisses after a few seconds (timer is in the store
// module).
//
// Mounted ONCE at the dashboard root (App.vue) so it overlays every route.

import { computed } from 'vue';
import { useStore } from 'vuex';
import { useRouter } from 'vue-router';
import { useMapGetter } from 'dashboard/composables/store';
import { getInboxIconByType } from 'dashboard/helper/inbox';
import Icon from 'next/icon/Icon.vue';

const store = useStore();
const router = useRouter();

const toasts = useMapGetter('visibleToasts');
const currentAccountId = useMapGetter('getCurrentAccountId');

// Map a channel_type ('Channel::Instagram', 'Channel::Email', etc.) to its
// remixicon name. Falls back to a generic chat icon when the channel is
// unknown (third-party future channels, etc.).
const channelIcon = channelType => getInboxIconByType(channelType) || 'i-ri-chat-3-line';

const openConversation = toast => {
  store.dispatch('dismissMessageToast', toast.id);
  if (!toast.conversationId || !currentAccountId.value) return;
  router.push(
    `/app/accounts/${currentAccountId.value}/conversations/${toast.conversationId}`
  );
};

const dismiss = (event, id) => {
  // Stop the click from bubbling to the parent (which would also try to
  // navigate). The dismiss-X is purely "go away" — no navigation.
  event.stopPropagation();
  store.dispatch('dismissMessageToast', id);
};

// Toasts is reactive; the template iterates `toasts.value` and Vue handles
// add/remove transitions for us via <TransitionGroup>.
const list = computed(() => toasts.value || []);
</script>

<template>
  <TransitionGroup
    tag="div"
    name="toast"
    class="fixed z-50 flex flex-col-reverse gap-2 bottom-4 right-4 w-80 pointer-events-none"
  >
    <div
      v-for="toast in list"
      :key="toast.id"
      role="button"
      tabindex="0"
      class="pointer-events-auto flex gap-3 p-3 rounded-lg shadow-lg cursor-pointer bg-n-solid-3 hover:bg-n-solid-active border border-n-weak"
      @click="openConversation(toast)"
      @keydown.enter="openConversation(toast)"
      @keydown.space.prevent="openConversation(toast)"
    >
      <div class="flex items-center justify-center shrink-0 rounded-full size-9 bg-n-alpha-2">
        <Icon :icon="channelIcon(toast.channelType)" class="text-n-slate-12" />
      </div>
      <div class="flex-1 min-w-0">
        <p class="mb-0.5 font-medium truncate text-n-slate-12">
          {{ toast.senderName }}
        </p>
        <p
          v-if="toast.contentPreview"
          class="text-sm line-clamp-2 text-n-slate-11"
        >
          {{ toast.contentPreview }}
        </p>
        <p v-else class="text-sm italic text-n-slate-11">
          (new message)
        </p>
      </div>
      <button
        type="button"
        class="self-start p-1 rounded text-n-slate-11 hover:text-n-slate-12 hover:bg-n-alpha-2"
        aria-label="Dismiss"
        @click="event => dismiss(event, toast.id)"
      >
        <Icon icon="i-lucide-x" class="size-4" />
      </button>
    </div>
  </TransitionGroup>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.25s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(20px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
