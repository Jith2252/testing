(() => {
  'use strict';

  const video = document.querySelector('#video');
  const videoFile = document.querySelector('#videoFile');
  const videoFileName = document.querySelector('#videoFileName');
  const previousButton = document.querySelector('#previousButton');
  const nextButton = document.querySelector('#nextButton');
  const playlistPanel = document.querySelector('#playlistPanel');
  const playlistCount = document.querySelector('#playlistCount');
  const playlistItems = document.querySelector('#playlistItems');
  const status = document.querySelector('#status');

  const state = { files: [], index: -1 };

  const setStatus = (message) => {
    status.textContent = message;
    status.classList.remove('error');
  };

  const isPlaying = () => !video.paused && !video.ended;

  const render = () => {
    const count = state.files.length;
    playlistPanel.hidden = count === 0;
    playlistCount.textContent = `${count} ${count === 1 ? 'video' : 'videos'}`;
    previousButton.disabled = state.index <= 0;
    nextButton.disabled = state.index < 0 || state.index >= count - 1;
    playlistItems.replaceChildren();

    state.files.forEach((file, index) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      item.className = 'playlist-item';
      button.type = 'button';
      button.textContent = file.name || `Video ${index + 1}`;
      button.title = file.name || `Video ${index + 1}`;
      button.addEventListener('click', () => select(index, isPlaying()));
      if (index === state.index) {
        item.classList.add('active');
        item.setAttribute('aria-current', 'true');
      }
      item.append(button);
      playlistItems.append(item);
    });
  };

  const select = (index, autoplay = false) => {
    if (index < 0 || index >= state.files.length) return;
    state.index = index;
    render();
    window.dispatchEvent(new CustomEvent('playlistselect', {
      detail: { file: state.files[index], index, total: state.files.length, autoplay },
    }));
  };

  const loadFiles = (fileList) => {
    state.files = Array.from(fileList || []);
    state.index = -1;
    render();
    if (!state.files.length) {
      videoFileName.textContent = 'Choose one or more files';
      return;
    }
    videoFileName.textContent = `${state.files.length} ${state.files.length === 1 ? 'file' : 'files'} selected`;
    setStatus(`${state.files.length} ${state.files.length === 1 ? 'video' : 'videos'} queued`);
    select(0);
  };

  videoFile.addEventListener('change', () => loadFiles(videoFile.files));
  previousButton.addEventListener('click', () => select(state.index - 1, isPlaying()));
  nextButton.addEventListener('click', () => select(state.index + 1, isPlaying()));
  window.addEventListener('playerended', () => {
    if (state.index < 0) return;
    if (state.index < state.files.length - 1) select(state.index + 1, true);
    else setStatus('Playlist complete');
  });

  render();
})();
