local socket = require("socket")
local json = require('json')
local math = require('math')

local STATE_OK = "Ok"
local STATE_TENTATIVELY_CONNECTED = "Tentatively Connected"
local STATE_INITIAL_CONNECTION_MADE = "Initial Connection Made"
local STATE_UNINITIALIZED = "Uninitialized"

local APIndex = 0x1A6E
local APItemAddress = 0x00FF
local EventFlagAddress = 0x1735
local MissableAddress = 0x161A
local HiddenItemsAddress = 0x16DE
local RodAddress = 0x1716
local InGame = 0x1A71

local ItemsReceived = nil
local playerName = nil
local seedName = nil

local prevstate = ""
local curstate =  STATE_UNINITIALIZED
local gbSocket = nil
local frame = 0

local u8 = nil
local wU8 = nil
local u16

--Sets correct memory access functions based on whether NesHawk or QuickNES is loaded
local function defineMemoryFunctions()
	local memDomain = {}
	local domains = memory.getmemorydomainlist()
	--if domains[1] == "System Bus" then
	--	--NesHawk
	--	isNesHawk = true
	--	memDomain["systembus"] = function() memory.usememorydomain("System Bus") end
	--	memDomain["saveram"]   = function() memory.usememorydomain("Battery RAM") end
	--	memDomain["rom"]       = function() memory.usememorydomain("PRG ROM") end
	--elseif domains[1] == "WRAM" then
	--	--QuickNES
	--	memDomain["systembus"] = function() memory.usememorydomain("System Bus") end
	--	memDomain["saveram"]   = function() memory.usememorydomain("WRAM") end
	--	memDomain["rom"]       = function() memory.usememorydomain("PRG ROM") end
	--end
	memDomain["rom"] = function() memory.usememorydomain("ROM") end
	memDomain["wram"] = function() memory.usememorydomain("WRAM") end
	return memDomain
end

local memDomain = defineMemoryFunctions()
u8 = memory.read_u8
wU8 = memory.write_u8
u16 = memory.read_u16_le
function uRange(address, bytes)
	data = memory.readbyterange(address - 1, bytes + 1)
	data[0] = nil
	return data
end


function table.empty (self)
    for _, _ in pairs(self) do
        return false
    end
    return true
end

function slice (tbl, s, e)
    local pos, new = 1, {}
    for i = s + 1, e do
        new[pos] = tbl[i]
        pos = pos + 1
    end
    return new
end

function processBlock(block)
    if block == nil then
        return
    end
    local itemsBlock = block["items"]
    memDomain.wram()
    if itemsBlock ~= nil then-- and u8(0x116B) ~= 0x00 then
	--	print(itemsBlock)
	ItemsReceived = itemsBlock

   end
end

function difference(a, b)
    local aa = {}
    for k,v in pairs(a) do aa[v]=true end
    for k,v in pairs(b) do aa[v]=nil end
    local ret = {}
    local n = 0
    for k,v in pairs(a) do
        if aa[v] then n=n+1 ret[n]=v end
    end
    return ret
end

function generateLocationsChecked()
	memDomain.wram()
	events = uRange(EventFlagAddress, 0x140)
	missables = uRange(MissableAddress, 0x20)
	hiddenitems = uRange(HiddenItemsAddress, 0x0E)
	rod = u8(RodAddress)

	data = {}

	table.foreach(events, function(k, v) table.insert(data, v) end)
	table.foreach(missables, function(k, v) table.insert(data, v) end)
	table.foreach(hiddenitems, function(k, v) table.insert(data, v) end)
	table.insert(data, rod)

    return data
end
function generateSerialData()
    memDomain.wram()
	status = u8(0x1A73)
    if status == 0 then
      return nil
    end
    return uRange(0x1A76, u8(0x1A74))
end
local function arrayEqual(a1, a2)
  if #a1 ~= #a2 then
    return false
  end

  for i, v in ipairs(a1) do
    if v ~= a2[i] then
      return false
    end
  end

  return true
end

function receive()
    l, e = gbSocket:receive()
    if e == 'closed' then
        if curstate == STATE_OK then
            print("Connection closed")
        end
        curstate = STATE_UNINITIALIZED
        return
    elseif e == 'timeout' then
        --print("timeout") -- this keeps happening for some reason? just hide it
        return
    elseif e ~= nil then
        print(e)
        curstate = STATE_UNINITIALIZED
        return
    end
    if l ~= nil then
        processBlock(json.decode(l))
    end
    -- Determine Message to send back
    memDomain.rom()
    newPlayerName = uRange(0xFFF0, 0x10)
    newSeedName = uRange(0xFFDB, 21)
    if (playerName ~= nil and not arrayEqual(playerName, newPlayerName)) or (seedName ~= nil and not arrayEqual(seedName, newSeedName)) then
        print("ROM changed, quitting")
        curstate = STATE_UNINITIALIZED
        return
    end
    playerName = newPlayerName
    seedName = newSeedName
    local retTable = {}
    retTable["playerName"] = playerName
    retTable["seedName"] = seedName
    memDomain.wram()
    if u8(InGame) == 0xAC then
        retTable["locations"] = generateLocationsChecked()
        serialData = generateSerialData()
        if serialData ~= nil then
        retTable["serial"] = serialData
        end
    end
    msg = json.encode(retTable).."\n"
    local ret, error = gbSocket:send(msg)
    if ret == nil then
        print(error)
    elseif curstate == STATE_INITIAL_CONNECTION_MADE then
        curstate = STATE_TENTATIVELY_CONNECTED
    elseif curstate == STATE_TENTATIVELY_CONNECTED then
        print("Connected!")
        curstate = STATE_OK
    end
end

function main()
    if (is23Or24Or25 or is26To28) == false then
        print("Must use a version of bizhawk 2.3.1 or higher")
        return
    end
    server, error = socket.bind('localhost', 17242)

    while true do
        if not (curstate == prevstate) then
            print("Current state: "..curstate)
            prevstate = curstate
        end
        if (curstate == STATE_OK) or (curstate == STATE_INITIAL_CONNECTION_MADE) or (curstate == STATE_TENTATIVELY_CONNECTED) then
            if (frame % 60 == 0) then
                receive()
                if u8(InGame) == 0xAC then
                    ItemIndex = u16(APIndex)
                    if ItemsReceived[ItemIndex + 1] ~= nil then
                        wU8(APItemAddress, ItemsReceived[ItemIndex + 1] - 172000000)
                    end
                end
            end
        elseif (curstate == STATE_UNINITIALIZED) then
            if  (frame % 60 == 0) then

                print("Waiting for client.")

                emu.frameadvance()
                server:settimeout(2)
                print("Attempting to connect")
                local client, timeout = server:accept()
                if timeout == nil then
                    -- print('Initial Connection Made')
                    curstate = STATE_INITIAL_CONNECTION_MADE
                    gbSocket = client
                    gbSocket:settimeout(0)
                end
            end
        end
        emu.frameadvance()
    end
end

main()
