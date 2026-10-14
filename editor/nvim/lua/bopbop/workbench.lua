-- One window for source buffers, evidence, tool discovery and model reviews.
local W = { buffers = {}, positions = {}, order = {}, revision = -1, generation = 0 }
local api = vim.api
local ns = api.nvim_create_namespace('bopbop-workbench')

local function request(data, callback)
  data.op = 'workbench'
  W.rpc(data, function(reply)
    if reply.error then W.notify(reply.error, vim.log.levels.WARN)
    elseif callback then callback(reply) end
  end)
end

function W.show(buf)
  local old = api.nvim_get_current_buf()
  W.positions[old] = vim.fn.winsaveview()
  vim.b[old].bopbop_view_status = vim.wo.statusline
  if vim.bo[old].buftype == '' then
    W.source = old
    vim.bo[old].bufhidden = 'hide'
  end
  if old ~= buf then
    vim.cmd.stopinsert()
    local mode = vim.fn.mode()
    if mode == 'v' or mode == 'V' or mode == '\22' then
      vim.cmd('normal! ' .. api.nvim_replace_termcodes('<Esc>', true, false, true))
    end
  end
  api.nvim_win_set_buf(0, buf)
  if vim.bo[buf].buftype == '' then
    vim.wo.statusline = ' File · %<%t %m · %l/%L · F2 docs · F3 review · F4 context · F5 file'
  elseif vim.b[buf].bopbop_snapshot then
    vim.wo.statusline = ' Evidence · %l/%L · V select · F8 attach · e edit source · q file'
  end
  if vim.b[buf].bopbop_view_status then vim.wo.statusline = vim.b[buf].bopbop_view_status end
  if W.positions[buf] then vim.fn.winrestview(W.positions[buf]) end
end

local function scratch(name)
  local buf = W.buffers[name]
  if not buf or not api.nvim_buf_is_valid(buf) then
    buf = api.nvim_create_buf(false, true)
    W.buffers[name] = buf
    table.insert(W.order, name)
    vim.bo[buf].bufhidden = 'hide'
    api.nvim_buf_set_name(buf, 'bopbop://workbench/' .. name)
    vim.keymap.set('n', 'q', function() request({ action = 'source' }) end,
      { buffer = buf, desc = 'Return to source' })
    if #W.order > 32 then
      for i, key in ipairs(W.order) do
        local candidate = W.buffers[key]
        if candidate ~= buf and candidate ~= api.nvim_get_current_buf()
          and (not W.agent.pending or candidate ~= W.agent.pending.buf) then
          if api.nvim_buf_is_valid(candidate) then api.nvim_buf_delete(candidate, { force = true }) end
          W.buffers[key], W.positions[candidate] = nil, nil
          table.remove(W.order, i)
          break
        end
      end
    end
  end
  return buf
end

function W.source_view()
  if W.source and api.nvim_buf_is_valid(W.source) then
    W.show(W.source)
    W.kind = 'source'
    vim.wo.statusline = ' File · %<%t %m · %l/%L · F2 docs · F3 review · F4 context · F5 file'
  else W.notify('Open a file with /editor first') end
end

local function open(path)
  local view = vim.b.bopbop_snapshot
  local line = view and type(view.path) == 'string' and vim.fn.fnamemodify(view.path, ':p') == path
    and math.max(1, vim.fn.line('.') - view.body_start) or nil
  local buf = vim.fn.bufadd(path)
  vim.fn.bufload(buf)
  W.show(buf)
  W.source = buf
  W.source_view()
  if line then api.nvim_win_set_cursor(0, { math.min(line, api.nvim_buf_line_count(buf)), 0 }) end
end

local function snapshot(view, revision)
  local pieces, generation = {}, W.generation
  local function read(offset)
    request({ action = 'read', revision = revision, offset = offset }, function(reply)
      if generation ~= W.generation then return end
      table.insert(pieces, reply.text)
      if reply.next and reply.next ~= vim.NIL then read(reply.next); return end
      local buf = scratch('view-' .. (type(view.snapshot) == 'string' and view.snapshot or view.category))
      local current = api.nvim_get_current_buf() == buf and vim.fn.winsaveview() or nil
      vim.bo[buf].modifiable = true
      api.nvim_buf_set_lines(buf, 0, -1, true, vim.split(table.concat(pieces), '\n', { plain = true }))
      vim.bo[buf].modifiable = false
      vim.bo[buf].filetype = view.category == 'context' and 'json' or 'markdown'
      vim.b[buf].bopbop_snapshot = view
      vim.keymap.set('n', 'e', function()
        if type(view.path) == 'string' then request({ action = 'open', path = view.path })
        else W.notify('This view has no source file; use /editor to choose one') end
      end, { buffer = buf, desc = 'Edit source file' })
      for _, entry in ipairs({ { '<M-Left>', 'older' }, { '<M-Right>', 'newer' } }) do
        vim.keymap.set('n', entry[1], function() request({ action = entry[2] }) end, { buffer = buf })
      end
      W.show(buf); W.kind = 'snapshot'
      if current then vim.fn.winrestview(current) end
      vim.wo.number = false
      vim.wo.wrap = view.category ~= 'file'
      vim.wo.statusline = ' ' .. view.category:upper() .. (view.pinned and ' · PINNED' or '')
        .. ' · %l/%L · V select · F8 attach · e edit source · q file'
    end)
  end
  read(0)
end

local function picker()
  local generation = W.generation
  request({ action = 'tools' }, function(reply)
    if generation ~= W.generation then return end
    local buf = scratch('tool-picker')
    local selected, matches = 1, {}
    local function render()
      if not api.nvim_buf_is_valid(buf) then return end
      local query = (api.nvim_buf_get_lines(buf, 0, 1, false)[1] or ''):lower()
      matches = {}
      for _, tool in ipairs(reply.tools) do
        if (tool.name .. ' ' .. tool.description):lower():find(query, 1, true) then
          table.insert(matches, tool)
        end
      end
      selected = math.max(1, math.min(selected, #matches))
      local rows, first = {}, math.max(1, selected - math.max(1, vim.o.lines - 9))
      for index = first, math.min(#matches, first + math.max(1, vim.o.lines - 7)) do
        local tool = matches[index]
        table.insert(rows, {{ (index == selected and '› ' or '  ') .. tool.name,
                             index == selected and 'PmenuSel' or 'Function' }})
      end
      if #matches == 0 then table.insert(rows, {{ 'No matching tools', 'Comment' }}) end
      api.nvim_buf_clear_namespace(buf, ns, 0, -1)
      api.nvim_buf_set_extmark(buf, ns, 0, 0, { virt_lines = rows })
    end
    vim.bo[buf].modifiable = true
    api.nvim_buf_set_lines(buf, 0, -1, true, { '' })
    api.nvim_clear_autocmds({ group = W.group, buffer = buf })
    api.nvim_create_autocmd({ 'TextChanged', 'TextChangedI' }, {
      group = W.group, buffer = buf, callback = function() selected = 1; render() end,
    })
    for _, mode in ipairs({ 'n', 'i' }) do
      for _, key in ipairs({ '<Up>', '<Down>' }) do
        vim.keymap.set(mode, key, function()
          selected = math.max(1, math.min(#matches, selected + (key == '<Up>' and -1 or 1))); render()
        end, { buffer = buf })
      end
      vim.keymap.set(mode, '<CR>', function()
        render()
        if matches[selected] then
          vim.cmd.stopinsert()
          request({ action = 'docs', name = matches[selected].name })
        end
      end, { buffer = buf })
      vim.keymap.set(mode, '<Esc>', function() vim.cmd.stopinsert(); request({ action = 'source' }) end, { buffer = buf })
    end
    W.show(buf); W.kind = 'docs'
    vim.wo.statusline = ' DOCS · type to filter · ↑↓ choose · Enter documentation · Esc file'
    render()
    api.nvim_win_set_cursor(0, { 1, 0 })
    vim.cmd.startinsert()
  end)
end

function W.selection()
  local view = vim.b.bopbop_snapshot
  if not view then return nil end
  local mode = vim.fn.mode()
  if mode ~= 'V' then error('Use V to select snapshot body lines') end
  local first, last = vim.fn.line('v'), vim.fn.line('.')
  if first > last then first, last = last, first end
  first, last = first - view.body_start, last - view.body_start
  if first < 1 or last > view.body_lines then error('Select numbered body lines, excluding headers and metadata') end
  local lines = api.nvim_buf_get_lines(0, first + view.body_start - 1, last + view.body_start, true)
  for i, line in ipairs(lines) do lines[i] = line:gsub('^%s*%d+ │ ', '') end
  return { buf = api.nvim_get_current_buf(), path = view.title, snapshot = view.snapshot,
    mode = 'evidence', range = { first - 1, 0, last, 0 }, text = table.concat(lines, '\n') }
end

function W.attach()
  local ok, selection = pcall(function() return W.selection() or W.agent.capture(false) end)
  if not ok then W.notify(selection); return end
  local data = selection.snapshot and { action = 'attach', snapshot = selection.snapshot,
    first = selection.range[1] + 1, last = selection.range[3] }
    or { action = 'attach_source', request = vim.tbl_extend('force', selection,
           { action = 'explain', prompt = 'Attach selected evidence' }) }
  request(data, function() W.notify('Selection attached · F6 to ask in chat') end)
end

function W.restore(buf)
  W.show(buf)
  local view = vim.b[buf].bopbop_snapshot
  request(view and { action = 'restore', snapshot = view.snapshot } or { action = 'source' })
end

function W.review(buf)
  W.generation = W.generation + 1
  W.show(buf); W.kind = 'review'
  request({ action = 'reviewing' })
end

function W.start(agent, rpc, notify)
  if W.started then return end
  W.started, W.agent, W.rpc, W.notify = true, agent, rpc, notify
  W.source = api.nvim_get_current_buf()
  W.group = api.nvim_create_augroup('BopbopWorkbench', { clear = true })
  local function poll()
    rpc({ op = 'workbench', action = 'sync', after = W.revision }, function(reply)
      if reply.error then
        W.started = false
        W.notify(reply.error .. ' · file buffers retained', vim.log.levels.WARN)
        return  -- a disconnected session does not produce an endless notification loop
      end
      if reply.view then
        W.revision = reply.revision
        W.generation = W.generation + 1
        local view = reply.view
        if view.kind == 'open' then open(view.path)
        elseif view.kind == 'source' then W.source_view()
        elseif view.kind == 'docs' then picker()
        elseif view.kind == 'snapshot' then snapshot(view, reply.revision)
        elseif view.kind == 'review' and agent.review and api.nvim_buf_is_valid(agent.review) then
          W.show(agent.review); W.kind = 'review'
        end
      end
      vim.defer_fn(poll, 180)
    end)
  end
  poll()
end

return W
