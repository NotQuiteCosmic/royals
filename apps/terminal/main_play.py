import royals_lib as RoyalsLib
from royals_lib import *
from royals_engine.hasher import *
from royals_engine import engine as Engine
from royals_engine import ai as artificialPlayer

# These two used to come in on `from Hasher import *` and `Engine.getOrigin`. Both print
# to a terminal or block on input(), so they moved out of the engine package when it
# became importable by a web worker. The behaviour is unchanged -- see display.py and
# prompt.py, which hold the original code verbatim.
from display import DisplayHashBoard, DisplayMoves, displaySquare
from prompt import getOrigin
setting = 0


if setting == 0:
	board = Entering_Board()
	running = True
	a = 3
	turn = 0

	# who is at the keyboard, indexed the way contr is: [blue, red]
	humanSides = [True, True]
	# 2 is too shallow to see a gather coming together and leaves games running forever
	aiDepth = 3
	passes = 0

	# How loosely the computer picks its entering squares, and the seed both that and the
	# Perlin tilt come off. A 2 player game asks neither question and reaches neither, but
	# they are named here so the entering loop below is not reading a name that may not exist.
	entryNoise = 0.0
	entrySeed = 0

	# The squares the last thing played touched, shaded by DisplayHashBoard. It is set
	# everywhere the board changes and nowhere else, so what it shades while a side is
	# choosing is always what the other side just did.
	lastMove = []

	hashModes = ["2 player", "1 player", "0 player"]
	print("WELCOME!\nWhat Game Mode Would You Like To Play?")
	for i in hashModes: print(" " + i)

	modeCheck = False
	while not modeCheck:
		gameMode = input().lower()
		if gameMode in hashModes: modeCheck = True
		else: print("Invalid Game Mode")
	gameMode = hashModes.index(gameMode)

	if gameMode == 1:
		sideCheck = False
		while not sideCheck:
			side = input("What side would you like to play? (blue/red)   ").lower()
			if side == "blue":
				humanSides = [True, False]
				sideCheck = True
			elif side == "red":
				humanSides = [False, True]
				sideCheck = True
			else: print("Blue or red.")

	if gameMode == 2: humanSides = [False, False]

	# The search roughly triples in cost per level, and where that becomes a wait depends
	# entirely on which engine is answering. With the compiled one (the royals-accel wheel):
	# 7 is about a fifth of a second, 8 under a second, 9 about three. Without it, in pure
	# Python, subtract three from all of those -- 6 is already a couple of seconds and 8 is
	# most of a minute.
	#
	# This comment used to say "3 about a second and a half, 4 closer to ten", which was the
	# pure-Python cost before the packed-int board and then the Rust port; both numbers had
	# been wrong for two rewrites. The prompt asks which engine is running rather than
	# guessing, so the advice it gives is about the machine it is actually on.
	if gameMode != 0:
		fast = artificialPlayer._accel.active()
		advice = "7 to 9 recommended" if fast else "4 or 5 recommended — no compiled engine here"
		depthCheck = False
		while not depthCheck:
			depthIn = input("How deep should the computer calculate? (%s)   " % advice)
			if depthIn.isdigit() and int(depthIn) > 0:
				aiDepth = int(depthIn)
				depthCheck = True
			else: print("Please give a whole number of turns.")

		# The entering heuristic is otherwise deterministic, so the computer opens the same
		# way every game. 0 is that fixed opening; 100 puts every piece on a square drawn at
		# random, and in between the computer picks more or less loosely off its own ranking.
		# The seed gets printed so an opening worth seeing again can be played again.
		noiseCheck = False
		while not noiseCheck:
			noiseIn = input("How varied should the computer's entering be? "
							"(0-100, 100 is entirely random, enter for 50)   ")
			if noiseIn == "":
				entryNoise = 0.5
				noiseCheck = True
			elif noiseIn.isdigit() and int(noiseIn) <= 100:
				entryNoise = int(noiseIn) / 100.0
				noiseCheck = True
			else: print("Please give a whole number from 0 to 100.")

		entrySeed = artificialPlayer.setEntryNoise(entryNoise)
		if entryNoise: print("Entering seed: " + str(entrySeed))


	####### ENTERING #######
	print("\n" + "ENTERING")
	print("Royals go on first, then the four pawns, then the spies, a piece at a time.")
	print("A royal or a pawn can't be entered touching something you already control,")
	print("your dragon included. A spy goes anywhere empty.")

	for enterIndex, step in enumerate(Engine.enteringSequence()):
		enterContr = step[0]
		piece = step[1]
		isSpy = (piece == SPY)

		if piece == ROYAL: pieceName = "Royal"
		elif piece == PAWNS: pieceName = "Pawn"
		else: pieceName = "Spy"

		if enterContr:
			sideColor = bcolors.CRED
			sideName = "Red"
		else:
			sideColor = bcolors.CBLUE
			sideName = "Blue"

		print("")
		DisplayHashBoard(board, lastMove)

		options = Engine.enteringOptions(board, enterContr, isSpy)

		# every piece is hemmed in, so this one sits the entering out
		if not options:
			print(sideName + " has nowhere legal to enter a " + pieceName + " -- skipped.")
			continue

		if humanSides[enterContr]:
			placed = False
			while not placed:
				where = input(sideColor + sideName + " " + pieceName + ": " + bcolors.CEND)
				square = AlgebraToSquare(where)

				if square == 0:
					print("Not a square on the board.")
				elif square in options:
					board = Engine.dropPiece(board, square, enterContr, piece)
					lastMove = [square]
					placed = True
				elif Check_For_Occupancy(Get_Space_Data(board, square)):
					print("That square is taken.")
				else:
					print("Too close to a piece you already control.")
		else:
			square = artificialPlayer.enterVaried(
				board, enterContr, piece, isSpy, entryNoise,
				Engine.entryRng(entrySeed, enterIndex))
			print(sideColor + sideName + " " + pieceName + bcolors.CEND
				  + " enters at " + IndexToAlg(square - 1).upper())
			board = Engine.dropPiece(board, square, enterContr, piece)
			lastMove = [square]

	# whoever entered second opens the game, so the side that placed the last spy moves now
	turn = 1

	# the history starts on the position entering left behind, so the first move can't be
	# undone back into it either
	Engine.koReset()
	artificialPlayer.newGame()
	Engine.koRecord(board)


	while running:

		if a < 3:
			square = input("what square's hash would you like to retrieve? ")
			square = AlgebraToSquare(square)
			print(square)

			if square != 0:
				tSquare = Get_Space_Data(board, square)
				print(format(tSquare, '013b') + " " + str(Parse_Space(tSquare)))

				board = Mod_Space(board, square, Build_Space(0, 1, 0, 0, 0))
		DisplayHashBoard(board, lastMove)

		if a >= 3:
			# blue opens; getOrigin and checkMoves both read blue as 0 and red as 1.
			contr = turn % 2
			if contr: print(bcolors.CRED + "Red to move" + bcolors.CEND)
			else: print(bcolors.CBLUE + "Blue to move" + bcolors.CEND)

			moved = False
			beforeMove = board
			# what the shading has to go back to if the ko rule takes the move back
			beforeLast = lastMove

			if humanSides[contr]:
				# `getOrigin`, not `Engine.getOrigin`. It moved to prompt.py when the engine
				# package became importable by a web worker -- it blocks on input(), which a
				# server has no answer for -- and this call site was left behind, so every
				# human turn raised AttributeError. See the import at the top of this file.
				tOrigin = getOrigin(board, contr)
				#print(Engine.sumWeight(tOrigin))
				# an origin carries its own square now, so there is nothing to resolve.
				origin = tOrigin[Engine.ORIGIN_SQUARE]
				movingPris = bool(tOrigin[Engine.ORIGIN_PRIS])
				moveArray = Engine.checkMoves(board, tOrigin, contr)
				DisplayMoves(moveArray, origin, contr)

				# an empty moveArray means that piece is stuck, so loop round and let them
				# pick a different space.
				if moveArray:
					possJumps = moveArray[0]
					possPushes = moveArray[1]
					possBreaks = moveArray[2]
					alphBreaks = moveArray[4]
					possFrees = moveArray[5]

					while not moved:
						dest = input("Move ('q' to choose another space): ")
						if dest.lower() == "q": break

						# a break is named by direction rather than by square, and "break left"
						# reads as well as "left".
						word = dest.lower()
						if word.startswith("break "): word = word[6:]

						if word in alphBreaks:
							# alphBreaks is built one entry per possBreaks entry, in step.
							board = Engine.exeBreak(board, origin, possBreaks[alphBreaks.index(word)], contr)
							# a break scatters the square rather than going anywhere in
							# particular, so the square it left is all there is to shade
							lastMove = [origin]
							moved = True
						else:
							# possJumps and possPushes are 0-based, AlgebraToSquare is 1-based.
							destSquare = AlgebraToSquare(dest)
							if destSquare == 0:
								print("Not a square on the board.")
							elif (destSquare - 1) in possJumps:
								board = Engine.exeMove(board, origin, destSquare, contr, movingPris)
								lastMove = [origin, destSquare]
								moved = True
							elif (destSquare - 1) in possPushes or (destSquare - 1) in possFrees:
								# A square can be in both lists at once: shoving the whole thing along
								# and freeing the allies held on it are two different moves onto it, so
								# naming the square is no longer enough to say which one was meant.
								canFree = (destSquare - 1) in possFrees
								canShove = (destSquare - 1) in possPushes
								freeing = canFree
								
								if canFree and canShove:
									answer = ""
									while not (answer.startswith("f") or answer.startswith("p")):
										answer = input("Free the prisoners, or shove the whole"
											   " square along? (free/push)   ").lower()
									freeing = answer.startswith("f")
								
								board = Engine.exePush(board, origin, destSquare, contr, movingPris, freeing)
								lastMove = [origin, destSquare]
								moved = True
							else:
								print("Not a legal move from " + IndexToAlg(origin - 1).upper() + ".")

			else:
				print("Thinking...")
				board, aiMove, aiScore = artificialPlayer.takeTurn(board, contr, aiDepth)

				if aiMove == None:
					# a side with nothing left to move just loses its turn -- without this
					# the loop would sit here redrawing the same board forever.
					print("  no legal move, passing.")
					# nothing moved, so there is nothing to shade
					lastMove = []
					passes += 1
					turn += 1
					if passes > 1:
						print("Game Finished!\nNeither side can move.")
						running = False
				else:
					print("  " + artificialPlayer.describeMove(aiMove))
					print("  " + bcolors.CGREY + "score " + artificialPlayer.scoreText(aiScore) + ", "
						  + str(artificialPlayer.calcCount) + " boards considered" + bcolors.CEND)

					aiOrigin = aiMove[artificialPlayer.MOVE_ORIGIN]
					# a break has a direction where the others have a square, so the square
					# it broke out of is the only one there is to shade
					if aiMove[artificialPlayer.MOVE_KIND] == "break": lastMove = [aiOrigin]
					else: lastMove = [aiOrigin, aiMove[artificialPlayer.MOVE_TARGET] + 1]

					moved = True

			# KO CHECK -- a move that returns the game to any position it has already been in
			# is taken back, and the side has another go. The AI is filtered at the root of
			# its search and so never gets here; this is what stops a human doing it.
			if moved and Engine.koBreaks(board):
				print(bcolors.CGREY + "  Ko: the game has already stood there. Move again."
					  + bcolors.CEND)
				board = beforeMove
				lastMove = beforeLast
				moved = False

			# only a completed move ends the turn.
			if moved:
				Engine.koRecord(board)
				passes = 0
				turn += 1

				# The delayed win: gathering does not end the game, surviving a reply does.
				# turn has just moved on, so this asks whether the side whose turn is about
				# to begin still has six on one square -- which means the opponent's answer
				# came and went. Both sides gathered is no longer a tie: the side to move is
				# the one whose stack survived, so it is theirs.
				winner = Check_For_Winner(board)[1]
				mover = turn % 2
				if winner[mover]:
					DisplayHashBoard(board, lastMove)
					print("Game Finished!")
					if mover == 0: print("Congratulations, " + blueT("Blue"))
					else: print("Congratulations, " + redT("Red"))
					running = False


		a += 1




if setting == 1:
	###############################
	####### MAIN PROGRAM ##########
	###############################
	print("WELCOME!\nWhat Game Mode Would You Like To Play?")
	for i in Modes : print(" " + i)
	modeCheck = False
	while not modeCheck:
		gameMode = input()
		if gameMode.lower() in Modes : modeCheck = True
		else : print("Invalid Game Mode")
	gameMode = Modes.index(gameMode)


	######################
	##### TWO PLAYER #####
	######################
	if gameMode == 0 or gameMode == 5:
		if gameMode == 0:
			# Beginning placement
			printBoard(board)
			placeBlue(2, "Blue Royal: ", False)

			printBoard(board)
			placeRed(2, "Red Royal: ", False)

			for i in range(0,4) :
				printBoard(board)
				placeBlue(1, "Blue Pawn: ", False)

				printBoard(board)
				placeRed(1, "Red Pawn: ", False)

			printBoard(board)
			placeBlue(0, "Blue Spy: ", False)

			printBoard(board)
			placeRed(0, "Red Spy: ", False)
		else: board = copy.deepcopy(tBoard)

		unn = input("Red, Begin Game By Entering Anything")

		# Main Gameplay Loop
		while not gameEnd:
			#PRINTS the board out if the origin wasn't reset.
			#flip-flops turns (for indexing) and prints the turn with the right color.
			if not reset :
				print("\n")
				printBoard(board)
				turn += 1
			else : reset = False

			if turn % 2 == 0 : print((bcolors.CBLUE + "Turn " + str((turn + 1) / 2) + bcolors.CEND))
			else : print((bcolors.CRED + "Turn " + str((turn + 1) / 2) + bcolors.CEND))


			moveCheck = False
			while not moveCheck :
				getOrigin(board, turn % 2)
				moveCheck = False
				moveArray = checkMoves(board, moveCoords, turn % 2)
				if moveArray != [] : moveCheck = True
				if moveCheck :
					possJumps = moveArray[0]
					possPushes = moveArray[1]
					possBreaks = moveArray[2]
					possMoves = moveArray[3]


			moveCheck = False

			while not moveCheck :
				brkPhrs = ["break up", "up", "break down", "down", "break left", "left", "break right", "right"]
				realMove = False
				while not realMove :
					destCoords = input("Move ('q' to change space): ")
					if destCoords.lower() == "q" or algToCoord(destCoords)[0] != -1 or destCoords.lower() in brkPhrs : realMove = True

				if destCoords.lower() == "q" :
					reset = True
					break

				elif destCoords.lower() == brkPhrs[0] or destCoords.lower() == brkPhrs[1] :
						exeBreak(board, moveCoords, [-1, 0], turn % 2)
						break
				elif destCoords.lower() == brkPhrs[2] or destCoords.lower() == brkPhrs[3] :
						exeBreak(board, moveCoords, [1, 0], turn % 2)
						break
				elif destCoords.lower() == brkPhrs[4] or destCoords.lower() == brkPhrs[5] :
						exeBreak(board, moveCoords, [0, -1], turn % 2)
						break
				elif destCoords.lower() == brkPhrs[6] or destCoords.lower() == brkPhrs[7] :
						exeBreak(board, moveCoords, [0, 1], turn % 2)
						break


				else :
					destCoords = algToCoord(destCoords)
					####EXECUTES THE TURN IF POSSIBLE

					if [destCoords[0], destCoords[1]] in possJumps :
						moveCheck = True
						exeMove(board, moveCoords, destCoords, turn % 2)

					elif [destCoords[0], destCoords[1]] in possPushes :
						moveCheck = True
						exePush(board, moveCoords, destCoords, turn % 2, True)
					#if move not in array of possible moves, prints an error message
					else : print("Invalid Move")

			if reset : continue

			#print(moveCoords)
			#Checks for win condition! CURRENTLY doesn't offer possibility to tie. Would like to change that.
			winner = [0,0]
			for i in board :
				for j in i :
					winCheck = j[turn % 2]
					if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
						gameEnd = True
						winner[turn % 2] = 1
					winCheck = j[(turn + 1) % 2]
					if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
						gameEnd = True
						winner[(turn + 1) % 2] = 1

		print("Game Finished!")
		if winner == [0,1] : print("Congratulations, " + blueT("Blue"))
		if winner == [1,0] : print("Congratulations, " + redT("Red"))
		if winner == [1,1] : print("There was a Tie.")
		if winner == [0,0] : print("Aw, shit. I fucked up really, really bad. Shit, shit, SHIT.")

	######################
	##### ONE PLAYER #####
	######################
	##NOTE this is where you can set the starting game board. Notably, these placement clauses should probably be one thing. For that matter, the main gameplay loops of the different modes could definitely be combined.
	elif gameMode == 1 or gameMode == 6:

		sideCheck = "bear"
		printSpaceInHash = True
		randomAll = True
		if gameMode == 6: useTBoard = True
		else : useTBoard = False

		while sideCheck.lower() != "blue" and sideCheck.lower() != "red" :
			sideCheck = input("What side would you like to play? (red/blue)   ")

		depth = int(input("How deep should the computer calculate? (recommended: 3, 4, or 5)   "))
		red = False
		if sideCheck.lower() == "red" : red = True

		if not randomAll and not useTBoard:
			# Beginning placement
			if not red : printBoard(board)
			placeBlue(2, "Blue Royal: ", red)

			if red : printBoard(board)
			placeRed(2, "Red Royal: ", not red)

			for i in range(0,4) :
				if not red : printBoard(board)
				placeBlue(1, "Blue Pawn: ", red)

				if red : printBoard(board)
				placeRed(1, "Red Pawn: ", not red)

			if not red : printBoard(board)
			placeBlue(0, "Blue Spy: ", red)

			if red : printBoard(board)
			placeRed(0, "Red Spy: ", not red)


		elif randomAll:
			# Beginning placement
			placeBlue(2, "Blue Royal: ", randomAll)


			placeRed(2, "Red Royal: ", randomAll)


			for i in range(0,4) :
				placeBlue(1, "Blue Pawn: ", randomAll)


				placeRed(1, "Red Pawn: ", randomAll)


			placeBlue(0, "Blue Spy: ", randomAll)


			placeRed(0, "Red Spy: ", randomAll)

		if useTBoard : board = copy.deepcopy(tBoard)

		printBoard(board)
		unn = input("Red, Begin Game By Entering Anything")

		# Main Gameplay Loop
		while not gameEnd:
			#PRINTS the board out if the origin wasn't reset.
			#flip-flops turns (for indexing) and prints the turn with the right color.
			if not reset :
				print("\n")
				printBoard(board)
				printHash(board, not printSpaceInHash)
				turn += 1
			else : reset = False

			if turn % 2 == 0 : print((bcolors.CBLUE + "Turn " + str((turn + 1) / 2) + bcolors.CEND))
			else : print((bcolors.CRED + "Turn " + str((turn + 1) / 2) + bcolors.CEND))

			### PLAYER MAKES A MOVE
			if (red and turn % 2 == 1) or (not red and turn % 2 == 0) :
				moveCheck = False
				while not moveCheck :
					getOrigin(board, turn % 2)
					moveCheck = False
					moveArray = checkMoves(board, moveCoords, turn % 2)
					if moveArray != [] : moveCheck = True
					if moveCheck :
						possJumps = moveArray[0]
						possPushes = moveArray[1]
						possBreaks = moveArray[2]
						possMoves = moveArray[3]
					print("POSSIBLE MOVES:")
					print("Jumps:")
					if possJumps == [] : print("NONE")
					for i in possJumps :
						print("- " + coordToAlg(i))
					print("Pushes:")
					if possPushes == [] : print("NONE")
					for i in possPushes :
						print("- " + coordToAlg(i))
					print("Breaks:")
					if possBreaks == [] : print("NONE")
					for i in possBreaks :
						print("- " + coordToAlg(i))
					print("~~~~~~")


				moveCheck = False

				while not moveCheck :
					brkPhrs = ["break up", "up", "break down", "down", "break left", "left", "break right", "right"]
					realMove = False
					while not realMove :
						destCoords = input("Move ('q' to change space): ")
						if destCoords.lower() == "q" or algToCoord(destCoords)[0] != -1 or destCoords.lower() in brkPhrs : realMove = True

					if destCoords.lower() == "q" :
						reset = True
						break

					elif (destCoords.lower() == brkPhrs[0] or destCoords.lower() == brkPhrs[1]) and [-1, 0] in possBreaks:
							exeBreak(board, moveCoords, [-1, 0], turn % 2)
							break
					elif (destCoords.lower() == brkPhrs[2] or destCoords.lower() == brkPhrs[3]) and [1, 0] in possBreaks:
							exeBreak(board, moveCoords, [1, 0], turn % 2)
							break
					elif (destCoords.lower() == brkPhrs[4] or destCoords.lower() == brkPhrs[5]) and [0, -1] in possBreaks:
							exeBreak(board, moveCoords, [0, -1], turn % 2)
							break
					elif (destCoords.lower() == brkPhrs[6] or destCoords.lower() == brkPhrs[7]) and [0, 1] in possBreaks:
							exeBreak(board, moveCoords, [0, 1], turn % 2)
							break
					elif destCoords.lower() in brkPhrs :
						print ("Invalid Move")
						continue


					else :
						destCoords = algToCoord(destCoords)
						####EXECUTES THE TURN IF POSSIBLE

						if [destCoords[0], destCoords[1]] in possJumps :
							moveCheck = True
							exeMove(board, moveCoords, destCoords, turn % 2)

						elif [destCoords[0], destCoords[1]] in possPushes :
							moveCheck = True
							exePush(board, moveCoords, destCoords, turn % 2, True)
						#if move not in array of possible moves, prints an error message
						else : print("Invalid Move")

				if reset : continue

			##### COMPUTER MAKES A MOVE
			else :

				calcCount = 0
				plan = minimax(board, turn % 2, -2048, 2048, depth, [], [], False)
				print("out, " + str(calcCount))
				if plan[5] : movingPris = True

				####### EXECUTES THAT SHIT ######
				print(plan[:len(plan) - 2])
				for i in range(0,len(plan[1])):
					if not plan[3] : print(str(plan[1][i]) + " to " + str(plan[2][i]))
					else : print(coordToAlg(plan[1][i]) + "-Break")
				#printBoard(plan[4])

				origin = plan[1][0]
				destination = plan[2][0]
				moveArray = checkMoves(board, origin, turn % 2)
				#print "move array: " + str(moveArray)
				possJumps = moveArray[0]
				possPushes = moveArray[1]

				#print possJumps,
				#print possPushes

				if destination in possJumps :
					print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Jump")
					exeMove(board, plan[1][0], plan[2][0], turn % 2)

				if destination in possPushes and not plan[3] :
					print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Push")
					exePush(board, plan[1][0], plan[2][0], turn % 2, True)

				if plan[3] :
					print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Break")
					exeBreak(board, plan[1][0], plan[2][0], turn % 2)

				movingPris = False

			#print(moveCoords)
			#Checks for win condition! CURRENTLY doesn't offer possibility to tie. Would like to change that. PSYCH I think it does???
			winner = [0,0]
			for i in board :
				for j in i :
					winCheck = j[turn % 2]
					if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
						gameEnd = True
						winner[turn % 2] = 1
					winCheck = j[(turn + 1) % 2]
					if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
						gameEnd = True
						winner[(turn + 1) % 2] = 1

		print("Game Finished!")
		if winner == [1,0] :
			printBoard(board)
			print("Congratulations, " + blueT("Blue"))
		if winner == [0,1] :
			printBoard(board)
			print("Congratulations, " + redT("Red"))
		if winner == [1,1] :
			printBoard(board)
			print("There was a Tie.")
		if winner == [0,0] : print("Aw, shit. I fucked up really, really bad. Shit, shit, SHIT.")

	#######################
	##### ZERO PLAYER #####
	#######################
	elif gameMode == 2:
		qSlow = input("Slow or fast?")
		if qSlow == "yes" or qSlow.lower == "slow" : isSlow = True
		else : isSlow = False

		depth = int(input("How deep should the computer calculate?   "))

		if randomStart :

			# Beginning placement
			#slow()
			placeBlue(2, "Blue Royal: ", True)

			#slow()
			placeRed(2, "Red Royal: ", True)

			for i in range(0,4) :
				#slow()
				placeBlue(1, "Blue Pawn: ", True)

				#slow()
				placeRed(1, "Red Pawn: ", True)

			#slow()
			placeBlue(0, "Blue Spy: ", True)

			#slow()
			placeRed(0, "Red Spy: ", True)

		else: board = copy.deepcopy(tBoard)

		printBoard(board)
		unn = input("Red, Begin Game By Entering Anything")

		# Main Gameplay Loop
		while not gameEnd:
			#PRINTS the board out if the origin wasn't reset.
			#flip-flops turns (for indexing) and prints the turn with the right color.
			if not reset :
				if isSlow or not isSlow:
					print("\n")
					printBoard(board)
				#for i in range(0,6) :
				#	print board[i]
				turn += 1
			else : reset = False

			if isSlow or not isSlow:
				if turn % 2 == 0 : print((bcolors.CBLUE + "Turn " + str((turn + 1) / 2) + bcolors.CEND))
				else : print((bcolors.CRED + "Turn " + str((turn + 1) / 2) + bcolors.CEND))

			######THIS IS WHERE THE MOVE IS CHOSEN########

			#plan = goDeep(board, turn % 2)
			calcCount = 0
			plan = minimax(board, turn % 2, -2048, 2048, depth, [], [], False)
			print("out, " + str(calcCount))
			if plan[5] :
				print("moving prisoners")
				movingPris = True

			####### EXECUTES THAT SHIT ######
			if isSlow or not isSlow:
				print(plan[:len(plan) - 2])
				for i in range(0, len(plan[1])):
					if not plan[3] : print(str(plan[1][i]) + " to " + str(plan[2][i]))
					else : print(coordToAlg(plan[1][i]) + "-Break")
				#printBoard(plan[4])

				if isSlow :uhh = input("->")

			origin = plan[1][0]
			destination = plan[2][0]
			moveArray = checkMoves(board, origin, turn % 2)
			print("move array: " + str(moveArray))
			possJumps = moveArray[0]
			possPushes = moveArray[1]

			print(possJumps, end=' ')
			print(possPushes)

			if destination in possJumps :
				print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Jump")
				exeMove(board, plan[1][0], plan[2][0], turn % 2)

			if destination in possPushes and not plan[3] :
				if isSlow or not isSlow: print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Push")
				exePush(board, plan[1][0], plan[2][0], turn % 2, True)
				#Does Ko Stuff

			if plan[3] :
				if isSlow or not isSlow: print(coordToAlg(plan[1][0]) + coordToAlg(plan[2][0]) + "-Break")
				exeBreak(board, plan[1][0], plan[2][0], turn % 2)

			####KO CHECK####

			if board == koTrack[0] :
				print("KO 1")
				board = copy.deepcopy(koTrack[0])
				reset = True
				koNO = [copy.copy(origin), copy.copy(destination)]

			elif board == koTrack[1] :
				print("KO 2")
				board = copy.deepcopy(koTrack[0])
				reset = True
				koNO = [copy.copy(origin), copy.copy(destination)]

			elif board == koTrack[2] :
				print("KO 3")
				board = copy.deepcopy(koTrack[0])
				reset = True
				koNO = [copy.copy(origin), copy.copy(destination)]

			elif board == koTrack[3] :
				print("KO 4")
				board = copy.deepcopy(koTrack[0])
				reset = True
				koNO = [copy.copy(origin), copy.copy(destination)]

			else :
				del koTrack[len(koTrack) - 1]
				koTrack.insert(0, copy.deepcopy(board))
				koNO = [[],[]]

			movingPris = False

			#Checks for win condition!
			gameEnd = checkFinished(board)[0]
			if gameEnd : winner = checkFinished(board)[1]

			#checks for lost pieces -- for debugging.
			container = 0
			for i in range(0,7) :
				for j in range(0,7) :
					for k in range(0,2) :
						container += countPieces(board[i][j][k])
			if newCount != container : badNEWS.append(turn / 2)
			newCount = container

			print(badNEWS)


		printBoard(board)
		print("Game Finished in " + str(turn / 2) + " turns ! ", end=' ')
		if winner == [0,1] : print("Congratulations, " + blueT("Blue"))
		if winner == [1,0] : print("Congratulations, " + redT("Red"))
		if winner == [1,1] : print("There was a Tie.")
		if winner == [0,0] : print("Aw, shit. I fucked up really, really bad. Shit, shit, SHIT.")




	#####################################
	#### random fast AND random slow ####
	#####################################
	elif gameMode in [3, 4] :
		for z in range(0,100) :
			print("----" + str(z) + "----")
			board = copy.deepcopy(bBoard)
			bplace = copy.deepcopy(bBplace)
			rplace = copy.deepcopy(bRplace)
			turn = 0
			winner = 0
			gameEnd = False

			isSlow = False
			if gameMode == 4 : isSlow = True
			# Beginning placement
			slow()
			placeBlue(2, "Blue Royal: ", True)

			slow()
			placeRed(2, "Red Royal: ", True)

			for i in range(0,4) :
				slow()
				placeBlue(1, "Blue Pawn: ", True)

				slow()
				placeRed(1, "Red Pawn: ", True)


			slow()
			placeBlue(0, "Blue Spy: ", True)

			slow()
			placeRed(0, "Red Spy: ", True)

			#printBoard(board)
			#unn = raw_input("Red, Begin Game By Entering Anything")

			# Main Gameplay Loop
			while not gameEnd :
				#PRINTS the board out if the origin wasn't reset.
				#flip-flops turns (for indexing) and prints the turn with the right color.
				if not reset :
					if isSlow :
						print("\n")
						printBoard(board)
					#for i in range(0,6) :
					#	print board[i]
					turn += 1
					#print turn
				else : reset = False

				if isSlow:
					if turn % 2 == 0 : print((bcolors.CBLUE + "Turn " + str((turn + 1) / 2) + bcolors.CEND))
					else : print((bcolors.CRED + "Turn " + str((turn + 1) / 2) + bcolors.CEND))


				moveCheck = False
				while not moveCheck :
					randOrigin()
					moveArray = checkMoves(board, moveCoords, turn % 2)
					moveCheck = False
					if moveArray != [] : moveCheck = True
					if moveCheck :
						possJumps = moveArray[0]
						possPushes = moveArray[1]
						possBreaks = moveArray[2]
						possMoves = moveArray[3]
						alphBreaks = moveArray[4]
					if isSlow : unn = input("->")

				moveCheck = False

				while not moveCheck :
					brkPhrs = ["break up", "up", "break down", "down", "break left", "left", "break right", "right"]

					destCoords = randMove()
					#print destCoords

					if isSlow :
						if destCoords in brkPhrs : print(destCoords)
						else : print(coordToAlg(destCoords))

					if destCoords == brkPhrs[1] :
							exeBreak(board, moveCoords, [-1, 0], turn % 2)
							break
					elif destCoords == brkPhrs[3] :
							exeBreak(board, moveCoords, [1, 0], turn % 2)
							break
					elif destCoords == brkPhrs[5] :
							exeBreak(board, moveCoords, [0, -1], turn % 2)
							break
					elif destCoords == brkPhrs[7] :
							exeBreak(board, moveCoords, [0, 1], turn % 2)
							break

					if destCoords in possJumps :
						moveCheck = True
						exeMove(board, moveCoords, destCoords, turn % 2)
						break


					elif destCoords in possPushes :
						moveCheck = True
						exePush(board, moveCoords, destCoords, turn % 2, True)
						break

					#if move not in array of possible moves, prints an error message
				if reset : continue

				with open("possibleStates.txt", 'r+') as file:
					x = 0
					for i in board :
						for j in i :
							#print j
							newSpace = list("...,...")
							if j[0][0] != 0 : newSpace[0] = "S"
							if j[0][2] != 0 : newSpace[1] = "R"
							if j[0][1] != 0 : newSpace[2] = str(j[0][1])
							if j[0][3] == 3 : newSpace[0] = "D"
							if j[1][0] != 0 : newSpace[4] = "S"
							if j[1][2] != 0 : newSpace[5] = "R"
							if j[1][1] != 0 : newSpace[6] = str(j[1][1])
							if j[1][3] == 3 : newSpace[4] = "D"
							newSpaceStr = "".join(newSpace)
							loopBreak = True
							#print newSpaceStr
							file.seek(0)
							text = file.read()
							lines = text.splitlines()
							for line in lines:
								if newSpaceStr == line :
									loopBreak = True

									#print "break"
									break
								else : loopBreak = False
							if not loopBreak :
								file.write(newSpaceStr + "\n")
								print(j)
								print(newSpaceStr)
								print("added")
							text = file.read()
							#print text + str(loopBreak)
					file.close()
					pass

				#Checks for win condition!
				winner = [0,0]
				for i in board :
					for j in i :
						winCheck = j[turn % 2]
						if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
							gameEnd = True
							winner[(turn + 1) % 2] = 1
						winCheck = j[(turn + 1) % 2]
						if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
							gameEnd = True
							winner[turn % 2] = 1

				container = 0
				for i in range(0,7) :
					for j in range(0,7) :
						for k in range(0,2) :
							container += countPieces(board[i][j][k])
				if newCount != container : badNEWS.append(turn / 2)
				newCount = container


			print(badNEWS)
			printBoard(board)
			print("Game Finished in " + str(turn / 2) + " turns ! ", end=' ')
			if winner == [0,1] : print("Congratulations, " + blueT("Blue"))
			if winner == [1,0] : print("Congratulations, " + redT("Red"))
			if winner == [1,1] : print("There was a Tie.")
			if winner == [0,0] : print("Aw, shit. I fucked up really, really bad. Shit, shit, SHIT.")

		counter = 0
		with open("possibleStates.txt", 'r') as file:
			text = file.read()
			lines = text.splitlines()
			for line in lines:
				counter += 1
		print(counter)

	else: print("That Game Mode Isn't Ready Yet.")


