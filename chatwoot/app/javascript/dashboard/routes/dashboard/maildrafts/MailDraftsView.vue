<script setup>
// Durian — the left-nav "Drafts" folder: saved-but-unsent composed emails,
// team-shared. Editing one emits COMPOSE_LOAD_DRAFT, which the always-mounted
// ComposeConversation (in the sidebar) listens for to reopen the composer
// pre-filled; sending it then removes the draft.
import { onMounted } from 'vue';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import { emitter } from 'shared/helpers/mitt';

const store = useStore();
const { t } = useI18n();
const drafts = useMapGetter('mailDrafts/getMailDrafts');
const uiFlags = useMapGetter('mailDrafts/getUIFlags');

onMounted(() => store.dispatch('mailDrafts/get'));

const recipients = draft =>
  (draft.to_emails || []).join(', ') || t('MAIL_DRAFTS.NO_RECIPIENT');

const editDraft = draft => emitter.emit('COMPOSE_LOAD_DRAFT', draft);

const deleteDraft = async id => {
  try {
    await store.dispatch('mailDrafts/delete', id);
    useAlert(t('MAIL_DRAFTS.DELETED'));
  } catch {
    useAlert(t('MAIL_DRAFTS.DELETE_ERROR'));
  }
};
</script>

<template>
  <div class="flex flex-col w-full h-full overflow-auto bg-n-background">
    <div class="flex items-center justify-between px-6 py-4">
      <h1 class="text-xl font-medium text-n-slate-12">
        {{ $t('MAIL_DRAFTS.HEADER') }}
      </h1>
    </div>

    <div
      v-if="uiFlags.isFetching"
      class="flex items-center justify-center flex-1 text-sm text-n-slate-11"
    >
      {{ $t('MAIL_DRAFTS.LOADING') }}
    </div>

    <div
      v-else-if="!drafts.length"
      class="flex items-center justify-center flex-1 text-sm text-n-slate-11"
    >
      {{ $t('MAIL_DRAFTS.EMPTY') }}
    </div>

    <ul v-else class="flex flex-col gap-2 px-6 pb-6">
      <li
        v-for="draft in drafts"
        :key="draft.id"
        class="flex items-start gap-3 p-4 rounded-lg outline-1 outline outline-n-weak bg-n-solid-1"
      >
        <div class="flex flex-col flex-1 min-w-0 gap-1">
          <span class="text-sm font-medium truncate text-n-slate-12">
            {{ draft.subject || $t('MAIL_DRAFTS.NO_SUBJECT') }}
          </span>
          <span class="text-xs truncate text-n-slate-11">
            {{ $t('MAIL_DRAFTS.TO') }} {{ recipients(draft) }}
          </span>
          <span class="text-xs truncate text-n-slate-10">
            {{ (draft.content || '').slice(0, 120) }}
          </span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            type="button"
            class="px-2.5 py-1 text-xs rounded-md outline-1 outline outline-n-container text-n-slate-11 hover:bg-n-alpha-2"
            @click="editDraft(draft)"
          >
            {{ $t('MAIL_DRAFTS.EDIT') }}
          </button>
          <button
            type="button"
            class="px-2.5 py-1 text-xs rounded-md outline-1 outline outline-n-container text-n-ruby-11 hover:bg-n-alpha-2"
            @click="deleteDraft(draft.id)"
          >
            {{ $t('MAIL_DRAFTS.DELETE') }}
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>
