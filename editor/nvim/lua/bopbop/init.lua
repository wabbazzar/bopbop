-- Explicit selection -> shared BopBop session -> review -> one undoable edit.
local M = { state = 'disconnected', pending = nil, review = nil }
local uv, api = vim.uv, vim.api
local defaults = { explain = '<leader>be', edit = '<leader>br', apply = '<leader>ba',
  discard = '<leader>bd', cancel = '<leader>bc', retry = '<leader>bt' }
local commands = { explain = 'BopbopExplain', edit = 'BopbopEdit', apply = 'BopbopApply',
  discard = 'BopbopDiscard', cancel = 'BopbopCancel', retry = 'BopbopRetry' }
local function id() return vim.fn.sha256(tostring(uv.hrtime()) .. tostring(math.random())):sub(1, 32) end
local function notify(text, level)
  M.message = text
  vim.notify('BopBop: ' .. text, level or vim.log.levels.INFO)
  vim.cmd.redrawstatus()
end
local function fail(text)
  M.state = 'retry available'
  notify(text .. ' · selection and prompt retained', vim.log.levels.WARN)
end
local function expand_key(key)
  return key:gsub('<leader>', vim.g.mapleader or '\\'):gsub('<localleader>', vim.g.maplocalleader or '\\')
end
local function key_hint(name, mode, buffer)
  local key = M.keys[name]
  if key and key ~= false then
    local found = vim.fn.maparg(expand_key(key), mode, false, true)
    if found.desc == 'BopBop ' .. name and (not buffer or found.buffer == 1) then
      return expand_key(key) .. ' ' .. name
    end
  end
  return ':' .. commands[name]
end
function M.hint()
  local parts = { '● bopbop · ' .. M.state }
  local mode = vim.fn.mode()
  if M.review and api.nvim_get_current_buf() == M.review then
    if M.pending and M.pending.action == 'edit' then table.insert(parts, key_hint('apply', 'n', true)) end
    table.insert(parts, key_hint('discard', 'n', true))
  elseif mode == 'v' or mode == 'V' or mode == '\22' then
    table.insert(parts, key_hint('explain', 'x'))
    table.insert(parts, mode == '\22' and 'v/V for edit' or key_hint('edit', 'x'))
  elseif M.state == 'working' then
    table.insert(parts, key_hint('cancel', 'n'))
  elseif M.state == 'retry available' then
    table.insert(parts, key_hint('retry', 'n'))
  else
    table.insert(parts, 'v/V select → explain / edit')
  end
  return (table.concat(parts, ' · '):gsub('%%', '%%%%'))
end

-- One RPC per pipe: bounded reply and timeout, no shell or subprocess required.
local function rpc(data, callback)
  if not M.socket or not M.session then callback({ error = 'Not connected; use :BopbopConnect SESSION' }); return end
  data.session, data.client = M.session, M.client
  local pipe, timer, finished, chunks, size = uv.new_pipe(false), uv.new_timer(), false, {}, 0
  local function finish(reply)
    if finished then return end
    finished = true
    timer:stop(); timer:close()
    pipe:read_stop(); pipe:close()
    vim.schedule(function() callback(reply) end)
  end
  timer:start(5000, 0, function() finish({ error = 'Connection timed out; reconnect and retry' }) end)
  pipe:connect(M.socket, function(err)
    if finished then return end
    if err then finish({ error = 'Disconnected: ' .. err }); return end
    pipe:read_start(function(readerr, chunk)
      if readerr then finish({ error = tostring(readerr) }); return end
      if not chunk then finish({ error = 'Connection closed' }); return end
      size = size + #chunk
      if size > 262144 then finish({ error = 'Bridge reply too large' }); return end
      table.insert(chunks, chunk)
      if chunk:find('\n', 1, true) then
        local ok, reply = pcall(vim.json.decode, table.concat(chunks))
        finish(ok and reply or { error = 'Invalid bridge reply' })
      end
    end)
    pipe:write(vim.json.encode(data) .. '\n', function(writeerr)
      if writeerr then finish({ error = tostring(writeerr) }) end
    end)
  end)
end

function M.capture(whole)
  local buf = api.nvim_get_current_buf()
  if vim.bo[buf].buftype ~= '' then error('Select code in a normal file buffer') end
  local name = api.nvim_buf_get_name(buf)
  if name == '' then error('Name this buffer with :file workspace/path first') end
  local mode, text, range, spans
  if whole then
    mode = 'buffer'
    local lines = api.nvim_buf_get_lines(buf, 0, -1, true)
    text, range = table.concat(lines, '\n'), { 0, 0, #lines, 0 }
  else
    local visual = vim.fn.mode()
    if visual ~= 'v' and visual ~= 'V' and visual ~= '\22' then
      error('Select with v or V first; :BopbopBufferExplain / :BopbopBufferEdit explicitly use the whole buffer')
    end
    local first, last = vim.fn.getpos('v'), vim.fn.getpos('.')
    local opts = { type = visual, exclusive = vim.o.selection == 'exclusive', eol = true }
    local segments = vim.fn.getregionpos(first, last, opts)
    local lines = vim.fn.getregion(first, last, { type = visual, exclusive = opts.exclusive })
    text = table.concat(lines, '\n')
    mode = visual == 'V' and 'line' or visual == 'v' and 'char' or 'block'
    local start, stop = segments[1][1], segments[#segments][2]
    if mode == 'line' then
      range = { start[2] - 1, 0, stop[2], 0 }
    else
      local line = api.nvim_buf_get_lines(buf, stop[2] - 1, stop[2], true)[1]
      local endbyte = math.min(#line, math.max(0, stop[3] - 1))
      if endbyte < #line then endbyte = endbyte + #vim.fn.strcharpart(line:sub(endbyte + 1), 0, 1, true) end
      range = { start[2] - 1, math.max(0, start[3] - 1), stop[2] - 1, endbyte }
    end
    spans = segments
  end
  if #text > 32768 then error('Selection exceeds 32 KiB; select a smaller region') end
  return { buf = buf, path = name, changedtick = api.nvim_buf_get_changedtick(buf),
    language = vim.bo[buf].filetype, mode = mode, text = text, range = range, spans = spans }
end

local function source_text(p)
  local r = p.range
  if p.mode == 'line' or p.mode == 'buffer' then
    return table.concat(api.nvim_buf_get_lines(p.buf, r[1], r[3], true), '\n')
  end
  return table.concat(api.nvim_buf_get_text(p.buf, r[1], r[2], r[3], r[4], {}), '\n')
end
local function map(name, mode, callback, buffer)
  local key = M.keys[name]
  if key and key ~= false and vim.fn.maparg(expand_key(key), mode) == '' then
    vim.keymap.set(mode, key, callback, { silent = true, buffer = buffer, desc = 'BopBop ' .. name })
  end
end
function M.close_review()
  if M.review and api.nvim_buf_is_valid(M.review) then api.nvim_buf_delete(M.review, { force = true }) end
  M.review = nil
end
function M.discard()
  if api.nvim_get_current_buf() ~= M.review then notify('Discard is available in the review buffer'); return end
  local p = M.pending
  rpc({ op = 'resolve', id = p.id, outcome = 'discarded' }, function() end)
  M.close_review(); M.pending = nil; M.state = 'connected'
  notify('Discarded · source buffer unchanged')
end
function M.apply()
  local p = M.pending
  if api.nvim_get_current_buf() ~= M.review or not p or p.action ~= 'edit' or not p.result then
    notify('Apply is available only in an edit review buffer'); return false
  end
  if not api.nvim_buf_is_valid(p.buf) or not api.nvim_buf_is_loaded(p.buf)
    or api.nvim_buf_get_name(p.buf) ~= p.path or api.nvim_buf_get_changedtick(p.buf) ~= p.changedtick then
    notify('Source changed since selection · discard and select again', vim.log.levels.WARN); return false
  end
  local ok, current = pcall(source_text, p)
  if not ok or current ~= p.text then
    notify('Selection no longer matches · discard and select again', vim.log.levels.WARN); return false
  end
  local lines = vim.split(p.result.replacement, '\n', { plain = true })
  local r = p.range
  -- A separate buffer call is one ordinary undo step; never :write or undojoin.
  local applied, err = pcall(api.nvim_buf_call, p.buf, function()
    if p.mode == 'line' or p.mode == 'buffer' then
      api.nvim_buf_set_lines(p.buf, r[1], r[3], true, p.result.replacement == '' and {} or lines)
    else
      api.nvim_buf_set_text(p.buf, r[1], r[2], r[3], r[4], lines)
    end
  end)
  if not applied then notify(tostring(err), vim.log.levels.ERROR); return false end
  rpc({ op = 'resolve', id = p.id, outcome = 'applied' }, function() end)
  M.close_review(); M.pending = nil; M.state = 'connected'
  api.nvim_set_current_buf(p.buf)
  notify('Applied to unsaved buffer · u undo · :w save')
  return true
end
local function review(p, result)
  p.result = result
  M.state = 'awaiting review'
  if vim.o.columns < 120 then
    vim.cmd('botright split')
    vim.cmd('resize ' .. math.max(8, math.floor(vim.o.lines * .55)))
  else
    vim.cmd('botright vsplit')
  end
  local buf = api.nvim_create_buf(false, true)
  M.review = buf
  api.nvim_win_set_buf(0, buf)
  vim.wo.wrap = true
  vim.wo.linebreak = true
  vim.wo.number = false
  vim.wo.relativenumber = false
  vim.wo.statusline = ' BopBop review · %l/%L · read only'
  api.nvim_buf_set_name(buf, 'bopbop://' .. p.id .. '/' .. p.action)
  vim.bo[buf].bufhidden = 'wipe'
  local header = { 'BopBop · ' .. p.action .. ' · NOT APPLIED',
    p.path .. ' · ' .. p.mode .. ' · ' .. #p.text .. ' bytes',
    'Prompt: ' .. p.prompt, '' }
  local body = result.diff and ((result.summary or '') .. '\n\n' .. result.diff) or result.answer or ''
  vim.list_extend(header, vim.split(body, '\n', { plain = true }))
  api.nvim_buf_set_lines(buf, 0, -1, true, header)
  vim.bo[buf].filetype = p.action == 'edit' and 'diff' or 'markdown'
  vim.bo[buf].modifiable = false
  if p.action == 'edit' then map('apply', 'n', M.apply, buf) end
  map('discard', 'n', M.discard, buf)
  notify('Response ready · ' .. (p.action == 'edit' and key_hint('apply', 'n', true) .. ' · ' or '')
    .. key_hint('discard', 'n', true))
end
local handle_reply
handle_reply = function(p, reply)
  if M.pending ~= p or M.state == 'cancelling' then return end
  if reply.error then
    if reply.status == 'failed' then p.id = nil end
    fail(reply.error); return
  end
  if reply.status == 'resolved' then fail('This review was already ' .. reply.outcome); return end
  if reply.status == 'review' then review(p, reply.result); return end
  M.state = 'working'; vim.cmd.redrawstatus()
  vim.defer_fn(function()
    if M.pending == p and M.state == 'working' then
      rpc({ op = 'poll', id = p.id }, function(next_reply) handle_reply(p, next_reply) end)
    end
  end, 180)
end
function M.retry()
  local p = M.pending
  if not p then notify('No retained request'); return end
  if M.state == 'working' or M.review then notify('Cancel or discard the current request first'); return end
  p.id = p.id or id()
  M.state = 'working'
  local request = {}
  for _, key in ipairs({ 'path', 'changedtick', 'language', 'mode', 'text', 'range', 'spans', 'action', 'prompt' }) do
    request[key] = p[key]
  end
  rpc({ op = 'submit', id = p.id, request = request }, function(reply) handle_reply(p, reply) end)
end
function M.request(action, whole, prompt)
  if M.pending then notify('A request is retained · retry it, or :BopbopForget before selecting again'); return end
  local ok, p = pcall(M.capture, whole)
  if not ok then notify(p, vim.log.levels.WARN); return end
  if action == 'edit' and p.mode == 'block' then notify('Blockwise edits: use v or V', vim.log.levels.WARN); return end
  if action == 'edit' then
    local exact, value = pcall(source_text, p)
    if not exact or value ~= p.text then notify('Virtual columns cannot be replaced exactly; use v or V without virtualedit'); return end
  end
  if not whole then
    api.nvim_feedkeys(api.nvim_replace_termcodes('<Esc>', true, false, true), 'nx', false)
  end
  p.action = action
  local function submit(value)
    if not value or value:match('^%s*$') then return end
    p.prompt = value; M.pending = p; M.retry()
  end
  local label = string.format('%s · %s · lines %d–%d · %d B · shared session / no tools: ',
    action, vim.fn.fnamemodify(p.path, ':t'), p.range[1] + 1,
    p.range[3] + ((p.mode == 'char' or p.mode == 'block') and 1 or 0), #p.text)
  if prompt then submit(prompt) else vim.ui.input({ prompt = label }, submit) end
end
function M.cancel()
  local p = M.pending
  if not p or not p.id then return end
  M.state = 'cancelling'
  rpc({ op = 'cancel', id = p.id }, function(reply)
    if M.pending ~= p then return end
    if reply.status == 'review' then M.state = 'working'; handle_reply(p, reply); return end
    if reply.status == 'failed' then p.id = nil end
    fail(reply.error or 'Cancelled')
  end)
end
function M.connect(session)
  if M.state == 'working' or M.state == 'cancelling' or M.review then
    notify('Cancel or discard before changing connections'); return
  end
  if not session:match('^[a-f0-9]+$') or #session ~= 32 then notify('Expected the full session ID from bopbop editor list'); return end
  local changed = M.session and M.session ~= session
  M.session = session
  local root = vim.env.BOPBOP_EDITOR_DIR or ('/tmp/bopbop-editor-' .. uv.getuid())
  M.socket = root .. '/' .. session .. '.sock'
  if changed and M.pending then M.pending.id = nil end
  rpc({ op = 'hello' }, function(reply)
    if reply.error then fail(reply.error) else
      M.state = M.pending and 'retry available' or 'connected'
      notify('Connected · ' .. reply.model)
    end
  end)
end
function M.setup(opts)
  if M.initialized then return end
  if vim.fn.has('nvim-0.11') ~= 1 then error('BopBop needs Neovim 0.11 or later') end
  M.initialized = true
  opts = opts or {}
  M.keys = vim.tbl_extend('force', defaults, opts.keys or {})
  M.client = id()
  local function command(name, callback, options)
    api.nvim_create_user_command(name, callback, options or {})
  end
  for _, action in ipairs({ 'explain', 'edit' }) do
    command(commands[action], function(arg)
      if arg.range > 0 then vim.cmd('normal! gv') end
      M.request(action, false)
    end, { range = true })
    command('BopbopBuffer' .. (action == 'edit' and 'Edit' or 'Explain'), function() M.request(action, true) end)
    map(action, 'x', function() M.request(action, false) end)
  end
  command('BopbopApply', M.apply); command('BopbopDiscard', M.discard)
  command('BopbopCancel', M.cancel); command('BopbopRetry', M.retry)
  command('BopbopReview', function()
    if M.pending and M.pending.result and (not M.review or not api.nvim_buf_is_valid(M.review)) then
      M.review = nil; review(M.pending, M.pending.result)
    end
  end)
  command('BopbopForget', function()
    if M.state == 'working' or M.state == 'cancelling' then notify('Cancel the request first'); return end
    M.close_review(); M.pending = nil; M.state = 'connected'
  end)
  command('BopbopConnect', function(arg) M.connect(arg.args) end, { nargs = 1 })
  command('BopbopStatus', function() notify(M.hint()) end)
  map('cancel', 'n', M.cancel); map('retry', 'n', M.retry)
  if opts.hints ~= false then
    vim.o.winbar = vim.o.winbar .. '%#DiagnosticInfo# %{v:lua.require("bopbop").hint()}%*'
  end
  if vim.env.BOPBOP_EDITOR_SESSION then M.connect(vim.env.BOPBOP_EDITOR_SESSION) end
end
return M
