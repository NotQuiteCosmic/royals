
### IMPORT A COUPLE OTHER FILES (mostly for navigation)
import random
import math
import copy
import builtins


### Some colors, not sure if they'll work or not.
class bcolors:
    CEND      = '\33[0m'
    CBOLD     = '\33[1m'
    CITALIC   = '\33[3m'
    CURL      = '\33[4m'
    CBLINK    = '\33[5m'
    CBLINK2   = '\33[6m'
    CSELECTED = '\33[7m'

    CBLACK  = '\33[30m'
    CRED    = '\33[31m'
    CGREEN  = '\33[32m'
    CYELLOW = '\33[33m'
    CBLUE   = '\33[34m'
    CVIOLET = '\33[35m'
    CBEIGE  = '\33[36m'
    CWHITE  = '\33[37m'

    CBLACKBG  = '\33[40m'
    CREDBG    = '\33[41m'
    CGREENBG  = '\33[42m'
    CYELLOWBG = '\33[43m'
    CBLUEBG   = '\33[44m'
    CVIOLETBG = '\33[45m'
    CBEIGEBG  = '\33[46m'
    CWHITEBG  = '\33[47m'

    CGREY    = '\33[90m'
    CRED2    = '\33[91m'
    CGREEN2  = '\33[92m'
    CYELLOW2 = '\33[93m'
    CBLUE2   = '\33[94m'
    CVIOLET2 = '\33[95m'
    CBEIGE2  = '\33[96m'
    CWHITE2  = '\33[97m'

    CGREYBG    = '\33[100m'
    CREDBG2    = '\33[101m'
    CGREENBG2  = '\33[102m'
    CYELLOWBG2 = '\33[103m'
    CBLUEBG2   = '\33[104m'
    CVIOLETBG2 = '\33[105m'
    CBEIGEBG2  = '\33[106m'
    CWHITEBG2  = '\33[107m'
    

# Board is the catch all nested list that stores all of the info of the current gamestate. It is divided into columns and rows (rows within columns), meaning that some Y,X [note order] corresponds with a square. Tracks in reading order across square.
# Each square's data notes the occupants of the square in two different 4 element arrays [spy, pawn(s), royal, and dragon]. The order is BLUE on left, RED on right.
bBoard=[[[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,3],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,3]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]]]



tBoard=[[[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,1,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,3]], [[0,1,0,0],[0,0,0,0]], [[1,3,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,4,1,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[1,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,3],[0,0,0,0]]]]



board =[[[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],

        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,3],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,3]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]],
        
        [[[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]], [[0,0,0,0],[0,0,0,0]],  [[0,0,0,0],[0,0,0,0]]]]

gameMode = 0
Modes = ["2 player", "1 player", "0 player", "random fast", "random slow", "test 2", "test 1"]
gameEnd = False
moveCheck = False
reset = False
movingPris = False
direction = []
winner = 0
move = ""
moveCoords = []
destCoords = []
possJumps = []
possPushes = []
possBreaks = []
alphBreaks = []
possMoves = []
badNEWS = []
turn = 0
diagonals = [[0,0], [1,1], [2,2], [3,3], [4,4], [5,5], [6,6], [0,6], [1,5], [2,4], [4,2], [5,1], [6,0]]
newCount = 14
 

#This stores the last 3(?) boards. Each turn, after executing a move, it checks to see if the new board is identical to any of the stored boards. if it is, then resets the move (using koTrack[0]) and 
koTrack = [[], [], [], []]
koNO = [[], []]

debMini = False
depth = 4
calcCount = 0



### Prints the Game Board out at its current state. Called after every move.
def printBoard(pBoard):
	print("     A       B       C       D       E       F       G   \n")
	rownum = 1
	for y in pBoard:
		print(rownum, end=' ')
		print(" ", end=' ')
		rownum += 1
		colnum = 0
		for x in y:
			colnum +=1
			if (rownum + colnum) % 2 != 0 : spaceColor = bcolors.CGREY
			else : spaceColor = bcolors.CWHITE
				
			if x[0][3] == 3: print(bcolors.CBLUE + "DRAGN  " + bcolors.CEND, end=' ')
			else:
				if x[0][0] == 1: print(bcolors.CBLUE + "S" + bcolors.CEND, end=' ')
				else: print(spaceColor + "." + bcolors.CEND, end=' ')
				if x[0][1] != 0: print(bcolors.CBLUE + str(x[0][1]) + bcolors.CEND, end=' ')
				else: print(spaceColor + "." + bcolors.CEND, end=' ')
				if x[0][2] == 1: print(bcolors.CBLUE + "R  " + bcolors.CEND, end=' ')
				elif x[0][3] == -1: print(spaceColor + ".x " + bcolors.CEND, end=' ')
				else: print(spaceColor + ".  " + bcolors.CEND, end=' ')
		print("")
		print("   ", end=' ')
		colnum = 0
		for x in y:
			colnum +=1
			if (rownum + colnum) % 2 != 0 : spaceColor = bcolors.CGREY
			else : spaceColor = bcolors.CWHITE

			if x[1][3] == 3: print(bcolors.CRED + "DRAGN  " + bcolors.CEND, end=' ')
			else:
				if x[1][0] == 1: print(bcolors.CRED + "S" + bcolors.CEND, end=' ')
				else: print(spaceColor + "." + bcolors.CEND, end=' ')
				if x[1][1] != 0: print(bcolors.CRED + str(x[1][1]) + bcolors.CEND, end=' ')
				else: print(spaceColor + "." + bcolors.CEND, end=' ')
				if x[1][2] == 1: print(bcolors.CRED + "R  " + bcolors.CEND, end=' ')
				elif x[1][3] == -1: print(spaceColor + ".x " + bcolors.CEND, end=' ')
				else: print(spaceColor + ".  " + bcolors.CEND, end=' ')
		print("\n")
	pass


def printHash(pBoard, spaces : bool):
	hash = ""
	for y in pBoard:
		for x in y:
			# if there are pieces in the space...
			if countPieces(x[0]) != 0 or countPieces(x[1]) != 0:
				hash += "1"

				# who has control?
				control = 0
				if countPieces(x[1]) != 0 and x[1][3] != -1:
					hash += "1"
					control = 1
				else :
					hash += "0"

				if spaces: hash += " "

				# dragon check
				if x[control][3] == 3:
					hash += "1"
				else:
					hash += "0"

				# royal check
				if x[control][2] == 1:
					hash += "1"
				else : hash += "0"

				# pawn check
				if x[control][1] != 0:
					binary = bin(x[control][1])
					hash += "000"[0:3-(len(binary) - 2)] + binary[2:len(binary)]

				# spy check
				if x[control][0] == 1:
					hash += "1"
				else : hash += "0"

				if spaces: hash += " "

				pris = abs(control - 1)
				# captured pieces?
				if x[pris][3] == -1:

					hash += "1"

					if spaces: hash += " "

					# NO ROYAL possible

					# pawn check
					if x[control][1] != 0:
						binary = bin(x[control][1])
						hash += "000"[0:3 - (len(binary) - 2)] + binary[2:len(binary)]

					# spy check
					if x[control][0] == 1:
						hash += "1"
					else:
						hash += "0"
				else:
					hash += "0"

				#voila! move to the next square!



			# if no pieces: not occupied!
			else:
				hash += "0"
				pass

			if spaces :
				hash += " / "

	print(hash)
	print(str(len(hash)))

# converts letter to number for coord.
def alphToNumb(letter):
	if   (letter.lower() == "a"): return 0
	elif (letter.lower() == "b"): return 1
	elif (letter.lower() == "c"): return 2
	elif (letter.lower() == "d"): return 3
	elif (letter.lower() == "e"): return 4
	elif (letter.lower() == "f"): return 5
	elif (letter.lower() == "g"): return 6
	else: return -1 
	pass

# converts Algebraic notation to coordinate notation. Correct notation is X#. If AT ALL wrong, returns moveCoords=[-1]
def algToCoord(algC):
	coordinates = [0]
	num = ["1", "2", "3", "4", "5", "6", "7"]
	alph = ["a", "b", "c", "d", "e", "f", "g"]
	##checks length
	if (len(algC) != 2) or not (algC[1] in num and algC[0] in alph):
		print("Invalid Format")
		return [-1]
		
	#this bit just parces the input one character at a time.
	#letter first!
	if (alphToNumb(algC[0]) == -1):
		print("Invalid Format")
		return [-1]
	else:
		coordinates[0] = (alphToNumb(algC[0]))
		
		#then the number
		if (int(algC[1]) < 1 or int(algC[1]) > 7):
			print("Invalid Format")
			return [-1]
		else : 
			coordinates.append(int(algC[1])-1)
			
			coordinates[0], coordinates[1] = coordinates[1], coordinates[0]
			return(coordinates)

#converts coordinate notation to algrebraic notation.
def coordToAlg(coord) :
	alph = "abcdefg"
	return(alph[coord[1]] + str(coord[0] + 1)) 	


def blueT(words) :
	return bcolors.CBLUE + str(words) + bcolors.CEND

def redT(words) :
	return bcolors.CRED + str(words) + bcolors.CEND


 
 
 
 
######## THE BIG STUFF ########
 
 
#Gets the space that the player is moving from.
##NOTE this seems like it would be better reversed. moveCheck set to false at the beginning, and then the few conditions where it would be true are checked. But it works, so all's good!
def getOrigin(cBoard, contr):
	global moveCoords
	global moveCheck
	global movingPris
	movingPris = False
	moveCheck = False
	while not moveCheck:
		#RESETS the moveCoords
		moveCoords = [-1];
		move = builtins.input("Space: ")
		
		moveCoords = algToCoord(move)
		if moveCoords[0] != -1 : moveCheck = True
		else : continue
		#Checks whether the pieces are captured, unless the player's spy is in that space..
		if cBoard[moveCoords[0]][moveCoords[1]][contr][3] == -1 and cBoard[moveCoords[0]][moveCoords[1]][contr][0] != 1: moveCheck = False
		#Checks whether there ARE pieces to move.
		if countPieces(cBoard[moveCoords[0]][moveCoords[1]][contr]) == 0 : moveCheck = False 

		#print moveCoords
		if moveCheck and cBoard[moveCoords[0]][moveCoords[1]][(contr + 1) % 2][3] == -1 :
			check = False
			while check == False :
				input = input("Bring prisoners? (y/n) ")
				if input.lower() == "y" or input == "yes" :
					check = True
					movingPris = True
				if input.lower() == "n" or input == "no":
					check = True
	
	
#This will eventually house ALL move restrictions for all pieces.
def checkMoves(cBoard, moveCoords, contr):
	#reset moveCheck for looping purposes.
	global moveCheck
	moveCheck = False
	oldCoords = [moveCoords[0], moveCoords[1]]
	oldSpace = cBoard[moveCoords[0]][moveCoords[1]]
	
	if not movingPris :
		pieceWeight = sumWeight(oldSpace[contr])
		if pieceWeight != 0 : moveCheck = True
	else :
		pieceWeight = sumWeight(oldSpace[contr]) - sumWeight(oldSpace[(contr + 1) % 2])
		if pieceWeight != 0 : moveCheck = True
	
	#if there's a spy in the stack that is moving, set spyBool to True for capturing purposes.
	spyBool = False 
	if oldSpace[contr][0] == 1: spyBool = True

	#same, but for dragon
	dragonBool = False
	if oldSpace[contr][3] == 3: dragonBool = True

	
	#if you're breaking with a captured spy, set this shit to Quack for a good time.
	spyCap = False
	if cBoard[moveCoords[0]][moveCoords[1]][contr][0] == 1 and countPieces(cBoard[moveCoords[0]][moveCoords[1]][contr]) == 1 : spyCap = True
	
	###### DIAGONAL JUMP SUITE ######
	#WHAT A PAIN. I think I've gotten corners, edges, and royals/dragons done.
	# And weight, captures, not capturing a stack that is capturing your pieces,
	# not allowing spy to capture anything. mark captures on the Dragon index,
	# since dragons can't capture or be captured. just a -1 in [3].
	# Option to subtract captured pieces from current weight AND allow you to bring them along.
	alphBreaks = []
	possJumps = []
	possPushes = []
	possBreaks = []
	possMoves = []
	dirs = [[1, 1], [1, -1], [-1, 1], [-1, -1]]


	for j in dirs :
		###down-right diagonal
		for i in range(1, pieceWeight + 1):
			
			#Sets correctDiag for use later.
			if j[0] + j[1] == 0 : correctDiag = [[0,6], [1,5], [2,4], [3,3], [4,2], [5,1], [6,0]]
			else : correctDiag = [[0,0], [1,1], [2,2], [3,3], [4,4], [5,5], [6,6]]
		
		
			#If on the cBoard, then all good. Sets check to the square.
			if ((moveCoords[0] + (i * j[0])) in range(0,7) and (moveCoords[1] + (i * j[1])) in range(0,7)) :
				check = cBoard[moveCoords[0] + (i * j[0])][moveCoords[1] + (i * j[1])]
				wrap = False
			#if on the correct diagonal, then sets the check to wrap.
			elif oldCoords in correctDiag:
				setCoords = wrapToBoard([moveCoords[0] + (i * j[0]), moveCoords[1] + (i * j[1])])			
				check = cBoard[setCoords[0]][setCoords[1]]
				wrap = True
			#if it goes off of the edge and isn't on the diagonal, break strand
			else: break
			
			#if there are ANY pieces present in the new space AND a spy is being moved, break
			if spyBool == True and (sumWeight(check[(contr + 1) % 2]) != 0 or sumWeight(check[contr]) != 0 ) : break
			#if there are ANY pieces present in the new space AND a dragon is being moved, continue
			if dragonBool == True and (sumWeight(check[(contr + 1) % 2]) != 0 or sumWeight(check[contr]) != 0): continue
			
			#if there are allies captured on the target square, break
			if check[contr][3] == -1:
				if dragonBool: continue
				else: break

			#DEBUG
			if j == [-1,-1]:
				pass
			#if there are any royals or dragons on the square, break
			if (check[0][2] != 0 or check[1][2] != 0) or (check[0][3] == 3 or check[1][3] == 3):
				if dragonBool: continue
				else: break
			
			#the wee movingPrisoners suite of breakages
			if movingPris :
				#if there are ANY ally pieces, BREAK IT.

				if sumWeight(check[contr]) + sumWeight(check[(contr + 1) % 2]) != 0 : break

			#if the weight of any defenders is greater than any attackers, break.
			if sumWeight(check[(contr + 1) % 2]) > pieceWeight : break


			#AFTER MOVING PAST ALL OF THAT, add that shit to the list!
			elif wrap : possJumps.append(setCoords)
			else : possJumps.append([moveCoords[0] + (i * j[0]), moveCoords[1] + (i * j[1])])
			
			if spyCap : possJumps = [];
	
	if gameMode == 0 or (gameMode in [2,3,4] and isSlow):
		#prints the jump options out one by one.
		if len(possJumps) :
			print("Jumps")
			for el in possJumps :
				if len(el) == 2 : print(" " + coordToAlg(el))
	
	###### ORTHO PUSH SUITE and BREAK SUITE ######
	#First task: make it check the directions and return range. Takes into account captured spies.
	dirs = [[0, 1], [0, -1], [1, 0], [-1, 0]]
	for direction in dirs:
		rangeCheck = findRange(cBoard, moveCoords, direction, contr)
		if rangeCheck != 0 and not spyCap: 
			possPushes.append([moveCoords[0] + direction[0], moveCoords[1] + direction[1]])
		if cBoard[moveCoords[0]][moveCoords[1]][contr][0] == 1 :
			rangeCheck = checkBreak(cBoard, moveCoords, direction, contr)
			if rangeCheck != 0 :
				possBreaks.append(direction)
			
	
	if gameMode == 0 or (gameMode in [2,3,4] and isSlow) :
		if len(possPushes) != 0 :
			print("Pushes")
			for el in possPushes :
				if len(el) == 2 : print(" " + coordToAlg(el))
	
	
	possMoves = possJumps + possPushes
	
	##### BREAK CHECKS ######
	alphBreaks = []
	if len(possBreaks) != 0 :
		for el in possBreaks :
			if el == [-1, 0] : alphBreaks.append("up")
			if el == [1, 0] : alphBreaks.append("down")
			if el == [0, -1] : alphBreaks.append("left")
			if el == [0, 1] : alphBreaks.append("right")
		
		if gameMode == 0 or (gameMode in [2,3,4] and isSlow) :
			print("Breaks:")
			for el in alphBreaks :
				print(" " + str(el))
		
		possMoves.append("break")
		
	
		

	###### Are there any possible moves? ######
	if len(possMoves) == 0 : return []
	else : return [possJumps, possPushes, possBreaks, possMoves, alphBreaks]
	
	
#Returns the integer range of a push. 
def findRange(cBoard, inputSquare, direction, contr):
	totalWeight = 0
	pRange = 0
	
	pushWeight = 0
	if movingPris : pushWeight = sumWeight(cBoard[inputSquare[0]][inputSquare[1]][contr]) - sumWeight(cBoard[inputSquare[0]][inputSquare[1]][(contr + 1) % 2])
	else : pushWeight = sumWeight(cBoard[inputSquare[0]][inputSquare[1]][contr])
	
	#checks for royal
	king = False
	if cBoard[inputSquare[0]][inputSquare[1]][contr][2] == 1 : king = True
	spy = False
	if cBoard[inputSquare[0]][inputSquare[1]][contr][0] == 1 and pushWeight == 1: spy = True
	dragon = False
	if cBoard[inputSquare[0]][inputSquare[1]][contr][3] == 3 : dragon = True

	
	for i in range(1,7) :
		if inputSquare[0] + (direction[0] * i) > 6 or inputSquare[0] + (direction[0] * i) < 0 or inputSquare[1] + (direction[1] * i) < 0 or inputSquare[1] + (direction[1] * i) > 6 :
			#if the adjecent square is off of the cBoard, then bad news bears.
			if i == 1: return(0)
			spaceToCheck = cBoard[wrapSingle(inputSquare[0] + (direction[0] * i))][wrapSingle(inputSquare[1] + (direction[1] * i))]
				
		else : spaceToCheck = cBoard[inputSquare[0] + (direction[0] * i)][inputSquare[1] + (direction[1] * i)]
		#print spaceToCheck
	
		#print inputSquare,
		#print i
		###if one square away and not moving any prisoners, then ignore the weight of prisoners that you are releasing.
		if i == 1 and not movingPris: 
			if spaceToCheck[contr][3] == -1 :
				totalWeight = sumWeight(spaceToCheck[(contr + 1) % 2])
				#if there's a king pushing, set the weight of any jailors equal to one.
				if king : totalWeight = 1 
				#if it's a dragon or spy pushing, then you can't push
				if spy or dragon : return 0
				#print str(totalWeight) + " adjacent"
			else : totalWeight += sumWeight(spaceToCheck[contr]) + sumWeight(spaceToCheck[(contr + 1) % 2])
			
			#if nothing next to the pieces, returns 0 (can't push)
			if totalWeight == 0 :
				#print str(direction) + " no push"
				return 0	

		
		###otherwise add ALL pieces' weights to the totalWeight.
		else : 
			#if the square is empty, end the loop. Otherwise add to totalWeight
			if sumWeight(spaceToCheck[contr]) + sumWeight(spaceToCheck[(contr + 1) % 2]) == 0 : break
			totalWeight += sumWeight(spaceToCheck[contr]) + sumWeight(spaceToCheck[(contr + 1) % 2])
			#print str(totalWeight) + " not adjacent"
	
		#if there's a spy, then the first space has 1 weight.
		if i == 1 and spy and spaceToCheck[contr][3] != -1:
			totalWeight = 1
			
		#if totalWeight is too big to push, then return 0 (can't push)
		if pushWeight < totalWeight : return 0
	
		#increases the total spaces that can be pushed.
		pRange += 1	
	return(pRange)

# returns the break range of a break.
##NOTE would probably work best as a recursive loop. i.e., breaks one space at a time until it can't anymore (blocked or out of pieces) and then it breaks. Wellll maybe not for check but execute break fosho.
def checkBreak(cBoard, inputSquare, direction, contr) : 
	
	if countPieces(cBoard[wrapSingle(inputSquare[0])][wrapSingle(inputSquare[1])][0]) + countPieces(cBoard[wrapSingle(inputSquare[0])][wrapSingle(inputSquare[1])][1]) == 0 :
		return 0
		
		#basically, if the square AFTER isn't clear, would return 0. Maybe useful? dunno.
		##NOTE it seems like this is actually all that's needed... as long as it has a space to break into there's kinda nothing else important for it to know prior to execution.
	#if countPieces(cBoard[wrapSingle(inputSquare[0] + direction[0])][wrapSingle(inputSquare[1] + direction[1])][0]) + countPieces(cBoard[wrapSingle(inputSquare[0] + direction[0])][wrapSingle(inputSquare[1] + direction[1])][1]) != 0 :
	
	breakRange = 0
	rewind = [True]
	
	# sets the local control variable (convenient for captured spy! It might already work :))
	if cBoard[inputSquare[0]][inputSquare[1]][contr % 2][3] == -1 : control = (contr + 1) % 2
	elif cBoard[inputSquare[0]][inputSquare[1]][(contr + 1) % 2][3] == -1 : control = contr % 2
	else :
		if countPieces(cBoard[inputSquare[0]][inputSquare[1]][contr % 2]) != 0 : control = contr % 2
		else : control = (contr + 1) % 2
	
	mainStor = cBoard[inputSquare[0]][inputSquare[1]][control]
	prisStor = cBoard[inputSquare[0]][inputSquare[1]][(control + 1) % 2]
		
	###the GAUNTLETTTT
	i = 0
	endCheck = False
	#prisoners drop first...
	while i <= countPieces(prisStor):
		targ = cBoard[wrapSingle(inputSquare[0] + direction[0] * i)][wrapSingle(inputSquare[1] + direction[1] * i)]
		
		#prob unnecessary, but maybe useful!
		if i % 7 != 0 :
			#if piece would capture more than possible, break
		
			if sumWeight(targ[control]) > (i / 7) + 1 :
				#print " failed pris capture"
				endCheck = True
				break
			#if there is a royal, then break
			if (targ[0][2] == 1 or targ[1][2] == 1) and i > 0:
				#print " failed pris royal"
				endCheck = True
				break
			if (targ[0][3] == 3 or targ[1][3] == 3) and i > 0:
				#print " failed pris dragon"
				endCheck = True
				break
				#if opponent's piece would sandwich one of your pieces, break 
			if targ[(control + 1) % 2][3] == -1 :
				endCheck = True
				break
				
		if sumWeight(targ[control]) != 0 : rewind.append(False)
		else : rewind.append(True)
		i += 1
			
			
	
	#...then drop non-prisoners
	while i < countPieces(prisStor) + countPieces(mainStor) and not endCheck:
		targ = cBoard[wrapSingle(inputSquare[0] + direction[0] * i)][wrapSingle(inputSquare[1] + direction[1] * i)]

		if i % 7 != 0 :
			#if piece would capture more than possible, break
			if sumWeight(targ[(control + 1) % 2]) > (i / 7) + 1:
				endCheck = True
				break
			#if there is a royal, then break
			if (targ[0][2] == 1 or targ[1][2] == 1) and i > 0:
				endCheck = True
				break
			if (targ[0][3] == 3 or targ[1][3] == 3) and i > 0:
				endCheck = True
				break
			#if your piece would sandwich opponent's piece, break
			if targ[control % 2][3] == -1 :
				endCheck = True
				break
				
		rewind.append(True)
		i += 1
		
	while False in rewind : 
		if rewind[i] : break
		i -= 1
	
	
	#returns whatever range it hit .
	return i



#This function performs a given move.
def exeMove(cBoard, moveCoords, destCoords, contr):
	if not movingPris :
		#adds each element of the old space to the new space.
		
		for i in range(0,4) :
			cBoard[destCoords[0]][destCoords[1]][contr][i] += cBoard[moveCoords[0]][moveCoords[1]][contr][i]
			
		#IF the new space has pieces in it that are captured, set that side's dragon slot to -1
		enemyWeight = sumWeight(cBoard[destCoords[0]][destCoords[1]][(contr + 1) % 2])	
		#pretty useful for looking at the space data
		if enemyWeight != 0 :
			cBoard[destCoords[0]][destCoords[1]][(contr + 1) % 2][3] = -1
	
		#resets the old space to empty.
		cBoard[moveCoords[0]][moveCoords[1]][contr] = [0,0,0,0]
	
		#"releases" any enemy prisoners.
		cBoard[moveCoords[0]][moveCoords[1]][(contr + 1) % 2][3] = 0
	
	else :
		for i in range(0,4) :
			cBoard[destCoords[0]][destCoords[1]][contr][i] += cBoard[moveCoords[0]][moveCoords[1]][contr][i]
			cBoard[destCoords[0]][destCoords[1]][(contr + 1) % 2][i] += cBoard[moveCoords[0]][moveCoords[1]][(contr + 1) % 2][i]
		
		cBoard[moveCoords[0]][moveCoords[1]] = [[0, 0, 0, 0],[0, 0, 0, 0]]

#executes pushes. Easy Peasy!
def exePush(cBoard, moveCoords, destCoords, contr, realBool):
	dir = findDirection(moveCoords, destCoords)
	pRange = findRange(cBoard, moveCoords, dir, contr)
		
	#if not a Spy push, do normal push stuff!
	if cBoard[moveCoords[0]][moveCoords[1]][contr] != [1, 0, 0, 0] :
	
		for k in range(0 , pRange) :
			d = pRange - k
		
			pris = False
			if cBoard[wrappedPush(moveCoords, 1, 0, dir)][wrappedPush(moveCoords, 1, 1, dir)][contr][3] == -1 : pris = True
				
			#if freeing prisoners, only move the enemy data.
			if not movingPris and d == 1 and pris:
				cBoard[wrappedPush(moveCoords, 2, 0, dir)][wrappedPush(moveCoords, 2, 1, dir)][(contr + 1) % 2] = cBoard[wrappedPush(moveCoords, 1, 0, dir)][wrappedPush(moveCoords, 1, 1, dir)][(contr + 1) % 2]
				cBoard[wrappedPush(moveCoords, 1, 0, dir)][wrappedPush(moveCoords, 1, 1, dir)][(contr + 1) % 2] = [0, 0, 0, 0]
				cBoard[wrappedPush(moveCoords, 1, 0, dir)][wrappedPush(moveCoords, 1, 1, dir)][contr][3] = 0
			#if not a "freeing prisoners" push, set equal to the next closer to you:
			else:
				#swap(cBoard[wrappedPush(d + 1, 0)][wrappedPush(d + 1, 1)], cBoard[wrappedPush(d, 0)][wrappedPush(d, 1)])
				cBoard[wrappedPush(moveCoords, d + 1, 0, dir)][wrappedPush(moveCoords, d + 1, 1, dir)] = cBoard[wrappedPush(moveCoords, d, 0, dir)][wrappedPush(moveCoords, d, 1, dir)]
				cBoard[wrappedPush(moveCoords, d, 0, dir)][wrappedPush(moveCoords, d, 1, dir)] = [[0, 0, 0, 0], [0, 0, 0, 0]]

	
		#adds the pieces to any pieces that had been captured.
		if not movingPris:
			for i in range(0,4) :
				cBoard[destCoords[0]][destCoords[1]][contr][i] += cBoard[moveCoords[0]][moveCoords[1]][contr][i]
			
			#clears the origin square
			cBoard[moveCoords[0]][moveCoords[1]][contr] = [0, 0, 0, 0]
	
			#"releases" any prisoners
			cBoard[moveCoords[0]][moveCoords[1]][(contr + 1) % 2][3] = 0
		#if moving prisoners
		else :
			#swap(cBoard[wrappedPush(0, 0)][wrappedPush(0, 1)], cBoard[wrappedPush(1, 0)][wrappedPush(1, 1)])
			cBoard[wrappedPush(moveCoords, 1, 0, dir)][wrappedPush(moveCoords, 1, 1, dir)] = cBoard[wrappedPush(moveCoords, 0, 0, dir)][wrappedPush(moveCoords, 0, 1, dir)]
			cBoard[wrappedPush(moveCoords, 0, 0, dir)][wrappedPush(moveCoords, 0, 1, dir)] = [[0, 0, 0, 0], [0, 0, 0, 0]]
	else :
		cBoard[wrapSingle(moveCoords[0] + dir[0] * 2)][wrapSingle(moveCoords[1] + dir[1] * 2)] = cBoard[moveCoords[0] + dir[0]][moveCoords[1] + dir[1]]
		cBoard[moveCoords[0] + dir[0]][moveCoords[1] + dir[1]] = cBoard[moveCoords[0]][moveCoords[1]]
		cBoard[moveCoords[0]][moveCoords[1]] = [[0, 0, 0, 0], [0, 0, 0, 0]]
		
		if cBoard[moveCoords[0]][moveCoords[1]][contr][3] == -1 :
			exeBreak(cBoard, [wrapSingle(destCoords[0] + dir[0]), wrapSingle(destCoords[1] + dir[1])], dir, (contr + 1) % 2)
		else :
			exeBreak(cBoard, [wrapSingle(destCoords[0] + dir[0]), wrapSingle(destCoords[1] + dir[1])], dir, contr)

#Performs the break
def exeBreak(cBoard, inputSquare, direction, contr) :
	
	#Sets control to accurately represent the prisoners/jailors
	##NOTE i think this works, at least it does in most tests, but Iiii'm suspicious. I feel like I somehow screwed it all up by doing this. Weee'll see!
	control = contr
	if cBoard[inputSquare[0]][inputSquare[1]][control][3] == -1 : control = (control + 1) % 2
	
	#stores the sets of pieces
	mainStor = cBoard[inputSquare[0]][inputSquare[1]][control]
	prisStor = cBoard[inputSquare[0]][inputSquare[1]][(control + 1) % 2]
	breakRange = checkBreak(cBoard, inputSquare, direction, control)

	if breakRange != 0 : cBoard[inputSquare[0]][inputSquare[1]] = [[0, 0, 0, 0], [0, 0, 0, 0]]
	
	for i in range(0, breakRange) :
		targSquare = [wrapSingle(inputSquare[0] + direction[0] * i), wrapSingle(inputSquare[1] + direction[1] * i)]
		
		
		#Made it through! What'll drop?
		if countPieces(prisStor) != 0 :
			dropping = prisStor
		else :
			dropping = mainStor

		if dropping[0] != 0 : drPlace = 0
		elif dropping[1] != 0 : drPlace = 1
		else : drPlace = 2

		#Does the dropping
		if countPieces(prisStor) != 0 :
			cBoard[targSquare[0]][targSquare[1]][(control + 1) % 2][drPlace] += 1
			prisStor[drPlace] -= 1
			if countPieces(cBoard[targSquare[0]][targSquare[1]][control % 2]) != 0 :
				cBoard[targSquare[0]][targSquare[1]][control % 2][3] = -1
		else :
			cBoard[targSquare[0]][targSquare[1]][control % 2][drPlace] += 1
			mainStor[drPlace] -= 1
			if countPieces(cBoard[targSquare[0]][targSquare[1]][(control + 1) % 2]) != 0 :
				cBoard[targSquare[0]][targSquare[1]][(control + 1) % 2][3] = -1
				
		#on the FINAL SPACE, if there is more than 1 piece in the combined storages, then drop all remaining pieces.
		if i + 1 == breakRange and countPieces(prisStor) + countPieces(mainStor) > 0 :
			for j in range(0,4) :
				cBoard[targSquare[0]][targSquare[1]][(control + 1) % 2][j] += prisStor[j]
				cBoard[targSquare[0]][targSquare[1]][control % 2][j] += mainStor[j]
				
				if countPieces(prisStor) == 0 :
					cBoard[targSquare[0]][targSquare[1]][(control + 1) % 2][3] = 0
		
	
	
	
	
	
	
###### UTILITIES ########
	
def swap(entry1, entry2) :
	entry1, entry2 = entry2, entry1
# wraps it all up into one thing (too late to think)
def wrappedPush(moveCoords, distance, axis, direction) :
	return wrapSingle(moveCoords[axis] + direction[axis] * distance)

# determines the unit vector for the direction that a push (or break, eventually) is going.
def findDirection (stCoords, endCoords) :
	return [endCoords[0] - stCoords[0], endCoords[1] - stCoords[1]]
	
#SUMS the values of a space and returns weight.
def sumWeight(spaceC) :
	#print(len(spaceC)),
	#print("yu")
	total = 0
	for i in range(0, len(spaceC) - 1):
		total += spaceC[i]
	if spaceC[3] == 3 : total += 3
	return(total)

def countPieces(spaceC) :
	total = 0
	for i in range(0, 3):
		total += spaceC[i]
	if spaceC[3] == 3 : total += 1
	return(total)


#Wraps a coordinate pair or a single number to the other side of the board.
def wrapToBoard(coords) :
	#print("n+ " + str(coords))
	return [(coords[0] + 14) % 7, (coords[1] + 14) % 7]


def wrapSingle(number) :
	return (number + 14) % 7		
		
def variance(data):
	n = len(data)
	mean = sum(data) / n
	return sum((x - mean) ** 2 for x in data) / (n)
	
def stdev(data):
	var = variance(data)
	std_dev = math.sqrt(var)
	return std_dev


def checkFinished(cBoard) :
	gameEnd = False
	winner = [0,0]
	for i in cBoard :
		for j in i :
			winCheck = j[0]
			if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
				gameEnd = True
				winner[1] = 1
			winCheck = j[1]
			if winCheck[0] == 1 and winCheck[1] == 4 and winCheck[2] == 1 :
				gameEnd = True
				winner[0] = 1
				
	return [gameEnd, winner]



##############################
####### Some AI Garbge #######
##############################

#This makes a list of all pieces the controller can move.
def makePossList(cBoard, contr) :
	possList = []
	for i in range(0, 7) :
		for j in range(0, 7) :
			if cBoard[i][j][contr][3] != -1 and countPieces(cBoard[i][j][contr]) != 0 : possList.append([i, j])
	return possList
	

#Takes some of what was in maximize. Does a series of branching move projections, returning the board and the FIRST move....?
def performOneStep(cBoard, contr, origin, move, moveArray) :
		global movingPris
		magicBoard = copy.deepcopy(cBoard)
		
		
		#This is the jump suite, and it (hopefully) includes the possibility of bringing prisoners with you.
		if move in moveArray[0] :
			exeMove(magicBoard, origin, move, contr)
			#newestBoard = hypothesize(cBoard, origin, move, 0, contr)			
		
		elif move in moveArray[1]:
			exePush(magicBoard, origin, move, contr, False)
			#newestBoard = hypothesize(cBoard, origin, move, 1, contr)			
		
		elif move in moveArray[4] :
			if move == "up" : direction =   (-1, 0)
			if move == "down" : direction =  (1, 0)
			if move == "right" : direction = (0, 1)
			if move == "left" : direction = (0, -1)
			exeBreak(magicBoard, origin, direction, contr)
			#newestBoard = hypothesize(cBoard, origin, move, 2, contr)

		return magicBoard


####I"M DOING SOMETHING POTENTIALLY STUPID
def minimax (cBoard, contr, alpha, beta, depthTrack, originsC, movesC, pris) : 
	global calcCount
	calcCount += 1
	if debMini : print(depthTrack)
		
	#IF at the bottom of the tree (as deep as it'll go), gets the relative value of the boardstate for the Controlling piece.
	#OR if the gameboard represents the end of a game.
	
	
	if checkFinished(cBoard)[0] == True and checkFinished(cBoard)[1][1] == 1 :
		#print "potential win for blue"
		if contr + (depth - depthTrack) % 2 == 0 : return [1000 * (depthTrack + 1), [], [], False, cBoard]
		if contr + (depth - depthTrack) % 2 == 1 : return [-1000 * (depthTrack + 1), [], [], False, cBoard]
		
	elif checkFinished(cBoard)[0] == True and checkFinished(cBoard)[1][0] == 1 :
		#print "potential win for red"
		if contr + (depth - depthTrack) % 2 == 0 : return [-1000 * (depthTrack + 1), [], [], False, cBoard]
		if contr + (depth - depthTrack) % 2 == 1 : return [1000 * (depthTrack + 1), [], [], False, cBoard]
	
	elif depthTrack == 0 :
		#print checkFinished(cBoard)[0]
		return [fullCheck(cBoard, (contr + depth) % 2), [], [], False, cBoard]
	
	if debMini : print(originsC, end=' ')
	if debMini : print(movesC)
	#Makes a array of child boards and, if at the HIGHEST depth (the move currently being considered), their relative origin and move values.
	children = []
	origins = makePossList(cBoard, contr)
	for origin in origins :
		#print "origin" + str(origin),
		moveArray = checkMoves(cBoard, origin, contr)
		#fixes "break"
		#if there are no moves past a certain point, then continues to the next origin. I feel like this pops up a LOT more than I was expecting. Maybe has to do with prisoners?
		if len(moveArray) == 0 :
			continue
		
		if len(moveArray[4]) != 0 :
			moveArray[3].remove("break")
			for el in moveArray[4] :
				moveArray[3].append(el)
		for move in moveArray[3] :
			
			if depthTrack == depth and [origin, move] == koNO : 
				print("ko no!")
				continue
			
			mBoard = performOneStep(cBoard, contr, origin, move, moveArray)
			
			#attaches the board to the array AND the origin and move that created it.
			children.append([copy.deepcopy(mBoard), origin, move, False])
		
		#if your pieces are capturing enemy pieces, then also add the jumps that include CARRYING the prisoners with you.
		if cBoard[origin[0]][origin[1]][(contr + 1) % 2][3] == -1 :
			global movingPris
			movingPris = True
			moveArray = checkMoves(cBoard, origin, contr)
			#if there are possible moves, then do this. The continue was fucking it up.
			if len(moveArray) != 0 :
				for move in moveArray[0] :
					if depthTrack == depth and [origin, move] == koNO : 
						print("ko no!")
						continue
					
					mBoard = performOneStep(cBoard, contr, origin, move, moveArray)
				
					children.append([copy.deepcopy(mBoard), origin, move, True])
				
				for move in moveArray[1] :
					if depthTrack == depth and [origin, move] == koNO : 
						print("ko no!")
						continue
					
					mBoard = performOneStep(cBoard, contr, origin, move, moveArray)
				
					children.append([copy.deepcopy(mBoard), origin, move, True])
				
			movingPris = False
			
			
	#Uses those children!
	#maximizing
	if (depth - depthTrack) % 2 == 0: 
		
		#This variable is updated for each child, finding the one that it highest.
		#[Score, originsF, movesF, are we breaking?, end board (I think), are we bringing prisoners.]
		maxEval = [-2048 * depth, [], [], False, [], False]
		for child in children :
			
			if debMini : 
				print(originsC, end=' ')
				print(movesC)
				print("depth " + str(depthTrack) + ", origin " + str(child[1]) + ", move " + str(child[2]))
				printBoard(cBoard)
				printBoard(child[0])
			
			originsF = copy.deepcopy(originsC)
			originsF.insert(0, child[1])
			movesF = copy.deepcopy(movesC)
			movesF.insert(0, child[2])
			
			
			eval = minimax(child[0], (contr + 1) % 2, alpha, beta, depthTrack - 1, originsF, movesF, False)
			#The eval is compared to the current maximum, taking the maximum. IF the new eval is higher than the old,
			#	maxEval's 1 and 2 index are set to the Origin and Move of the evaluated board.
			
			if maxEval[0] <= eval[0] :
				maxEval[0] = eval[0]
				
				#Checks if the prisoners are being moved by the current MaxEval. Keep in mind that this isn't needed for the MinEval - it's never the minEval's turn.
				if depthTrack == depth:
					if child[3] : maxEval[5] = True
					else : maxEval[5] = False
				
				maxEval[1] = copy.copy(eval[1])
				maxEval[1].insert(0,copy.copy(child[1]))
			
				maxEval[2] = copy.copy(eval[2])
				maxEval[2].insert(0,copy.copy(child[2]))
				
				maxEval[4] = copy.deepcopy(eval[4])
				
				if  maxEval[2][0] == "up"    :
					maxEval[3] = True
					maxEval[2][0] = [-1,0]
				elif  maxEval[2][0] == "down"  :
					maxEval[3] = True
					maxEval[2][0] = [1,0]
				elif  maxEval[2][0] == "left"  :
					maxEval[3] = True
					maxEval[2][0] = [0,-1]
				elif  maxEval[2][0] == "right" :
					maxEval[3] = True
					maxEval[2][0] = [0,1]
				else : maxEval[3] = False
					
				
				
				
			#This is what confuses me. Basically, it checks the current board's value against beta,
			#	and if it's greater than beta (a value that is inhereted from the opponent's higher decision making),
			#	then the rest of the tree is ignored. I think that this is because a opponent would always take
			#	beta over alpha anyway, and you would always get to AT LEAST alpha, so the opponent knows that there's
			#	no point going down this path.
			alpha = max([alpha, eval[0]])
			if beta <= alpha :
				if debMini : print ("pruned")
				break
		
		if debMini : print("max")
		#print ("depth ") + str(depthTrack)
		if debMini : print(("  after: ") + str(maxEval[0:len(maxEval)-1]))
			
		return maxEval
	
	#minimizing
	else :
		
		minEval = [2048 * depth, [], [], False, [], False]
		for child in children :
			
			originsF = copy.deepcopy(originsC)
			originsF.insert(0, child[1])
			movesF = copy.deepcopy(movesC)
			movesF.insert(0, child[2])
			
			if debMini : 
				print("depth " + str(depthTrack) + ", origin " + str(child[1]) + ", move " + str(child[2]))
				printBoard(cBoard)
				printBoard(child[0])
		
			eval = minimax(child[0], (contr + 1) % 2, alpha, beta, depthTrack - 1, originsF, movesF, False)
			
			
			#Sets up the new slot for a stored move
			
			
			if minEval[0] >= eval[0] :
				minEval[0] = eval[0]
				
				if debMini : 
					print((child[1]), end=' ')
					print((child[2]))
				
				#Can't happen cause maximizing always first.
				#if depthTrack == depth and child[3] : minEval[5] = True

				
				minEval[1] = copy.copy(eval[1])
				minEval[1].insert(0,copy.copy(child[1]))
			
				minEval[2] = copy.copy(eval[2])
				minEval[2].insert(0,copy.copy(child[2]))

				
				minEval[4] = copy.deepcopy(eval[4])
				
				if  minEval[2][0] == "up"    :
					minEval[3] = True
					minEval[2][0] = [-1,0]
				elif  minEval[2][0] == "down"  :
					minEval[3] = True
					minEval[2][0] = [1,0]
				elif  minEval[2][0] == "left"  :
					minEval[3] = True
					minEval[2][0] = [0,-1]
				elif  minEval[2][0] == "right" :
					minEval[3] = True
					minEval[2][0] = [0,1]
				else : minEval[3] = False
					
				
				
			beta = min([beta, eval[0]])
			if beta <= alpha :
				if debMini : print ("pruned")
				break
		
		if debMini : 
			print("min")
			print(("depth ") + str(depthTrack))
			print(("  after: ") + str(minEval[0:len(minEval)-1]))
		
		#print maxEval
		return minEval
	




def checkPosition(cBoard, contr) :
	
	adv = 0
	whiteSq = 0
	blackSq = 0
	xTrack = []
	yTrack = []
	track = 0
	groups = 0
	royalIdiot = False
	for i in range(0,7):
		for j in range(0,7):
			#Checks the number of pieces on the square
			#print (cBoard[i][j][contr]),
			myPieces = countPieces(cBoard[i][j][contr % 2])
			prisoners = countPieces(cBoard[i][j][(contr + 1) % 2])
			
			if myPieces != 0 and cBoard[i][j][contr][3] != 3: 
				groups += 1
				if myPieces < 6 :
					#print (myPieces * myPieces)
					adv += myPieces * myPieces
				if myPieces == 6: return 1000
			
			if myPieces != 0 and cBoard[i][j][contr % 2][3] != 3: groups += 1
			
			#checks for prisoners and adds that many pieces to your advantage
			if cBoard[i][j][(contr + 1) % 2][3] == -1 : adv += prisoners * 0.8 
			#check if YOUR pieces are prisoners.
			if cBoard[i][j][contr % 2][3] == -1 : adv -= myPieces * (myPieces / 0.5)
			
			#DON"T PUT UR DANG ROYAL AND SPY IN THE SAME PLACE
			if cBoard[i][j][contr % 2][0] == 1 and cBoard[i][j][contr][2] == 1 and myPieces != 6 : royalIdiot = True 
			
			
			#adds stuff to the x/y tracks to incentivize moving stuff closer together. BUT YOU DON"T CARE BOUT THE DRAGON.
			if myPieces != 0 and cBoard[i][j][contr % 2][3] != 3: 
				xTrack.append(j)
				yTrack.append(i)
			
			#used to check what spaces your pieces are on.
			if (i + j) % 2 == 0 : whiteSq += countPieces(cBoard[i][j][contr])
			if (i + j) % 2 == 1 : blackSq += countPieces(cBoard[i][j][contr])
			
		#print "\n"
		
	#subtracts standard deviation of your pieces.
	#print (stdev(xTrack) * 1.5)
	#print (stdev(yTrack) * 1.5)
	adv -= groups * 1.5
	adv -= max(stdev(xTrack), stdev(yTrack)) * 1.5 
	
	#if the royal and the spy are together, subtract an amount that gets larger as the number groups gets smaller.
	if royalIdiot: adv -= (groups * groups)*5
	 
	#are your pieces on similar squares? Gets more important if you're more and more stacked up.
	#print "value of square sim: " + str(abs((blackSq * 1.2) - whiteSq) * (5 / groups))
	adv += abs((blackSq * 1.2) - whiteSq) * (5 / groups) 
	
	return(adv)


def fullCheck(cBoard, contr) :
	you      = checkPosition(cBoard, contr)
	opponent = checkPosition(cBoard, (contr + 1) % 2)
	if debMini : 
		print(you, end=' ')
		print(opponent)
	return you - opponent


#### Placing Stuff #######

bRplace=[[0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 1, 1, 1, 0],
		 [0, 0, 2, 1, 2, 1, 0],
		 [0, 0, 0, 1, 1, 1, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0]]

bBplace=[[0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 1, 1, 1, 0, 0, 0],
		 [0, 1, 2, 1, 2, 0, 0],
		 [0, 1, 1, 1, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0]]

rplace =[[0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 1, 1, 1, 0],
		 [0, 0, 2, 1, 2, 1, 0],
		 [0, 0, 0, 1, 1, 1, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0]]

bplace =[[0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 1, 1, 1, 0, 0, 0],
		 [0, 1, 2, 1, 2, 0, 0],
		 [0, 1, 1, 1, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0],
		 [0, 0, 0, 0, 0, 0, 0]]

adj = [[-1,-1], [-1,0], [-1,1],
	   [0, -1],         [0 ,1],
	   [1, -1], [1, 0], [1 ,1]]

def placeBlue(pieceSlot, text, random) :
	global board
	global bplace
	global rplace
	coord = []
	
	
	#if controlled
	if not random : 
		place = False
		while not place :
			placement = input(blueT(text))
			coord = algToCoord(placement)
			if pieceSlot == 0 and coord[0] != -1 and bplace[coord[0]][coord[1]] != 2: place = True
			elif pieceSlot != 0 and coord[0] != -1 and bplace[coord[0]][coord[1]] == 0: place = True
			else: print("Invalid Placement")
	
	#if NOT controlled
	else:
		if pieceSlot != 0 : coord = randPlacement(0, False)
		else : coord = randPlacement(0, True)
	
	#tosses it onto the actual board
	board[coord[0]][coord[1]][0][pieceSlot] = 1
	
	#tracks on placement boards
	rplace[coord[0]][coord[1]] = 2
	bplace[coord[0]][coord[1]] = 2
	for dir in adj :
		if coord[0] + dir[0] < 0 or coord[0] + dir[0] > 6 : continue
		if coord[1] + dir[1] < 0 or coord[1] + dir[1] > 6 : continue
		if bplace[coord[0] + dir[0]][coord[1] + dir[1]] == 2: continue
		
		bplace[coord[0] + dir[0]][coord[1] + dir[1]] = 1
			
	
def placeRed(pieceSlot, text, random) :
	global board
	global bplace
	global rplace
	coord = []
	
	#if controlled by player
	if not random : 
		place = False
		while not place :
			placement = input(redT(text))
			coord = algToCoord(placement)
			if pieceSlot == 0 and coord[0] != -1 and rplace[coord[0]][coord[1]] != 2: place = True
			elif pieceSlot != 0 and coord[0] != -1 and rplace[coord[0]][coord[1]] == 0: place = True
			else: print("Invalid Placement")
				
	#if NOT controlled
	else:
		if pieceSlot != 0 : coord = randPlacement(1, False)
		else : coord = randPlacement(1, True)

	#tosses it onto the actual board
	board[coord[0]][coord[1]][1][pieceSlot] = 1

	#tracks on placement boards
	bplace[coord[0]][coord[1]] = 2
	rplace[coord[0]][coord[1]] = 2
	for dir in adj :
		if coord[0] + dir[0] < 0 or coord[0] + dir[0] > 6 : continue
		if coord[1] + dir[1] < 0 or coord[1] + dir[1] > 6 : continue
		if rplace[coord[0] + dir[0]][coord[1] + dir[1]] == 2: continue
		
		rplace[coord[0] + dir[0]][coord[1] + dir[1]] = 1
	#for i in range(0,7) :
	#	print rplace[i]





######### RANDOM FUNCTIONS######
#places pieces in the beginning
def randPlacement(player, spy) :
	tries = 0
	pBad = True

	if not spy :
		while pBad :
			tries += 1
			ranCoords = [random.randrange(0,7), random.randrange(0,7)]
			if player == 0 and bplace[ranCoords[0]][ranCoords[1]] == 0 and rplace[ranCoords[0]][ranCoords[1]] != 2 : return ranCoords
			if player == 1 and rplace[ranCoords[0]][ranCoords[1]] == 0 and bplace[ranCoords[0]][ranCoords[1]] != 2: return ranCoords
	else : 
		while pBad :
			tries += 1
			ranCoords = [random.randrange(0,7), random.randrange(0,7)]
			if player == 0 and bplace[ranCoords[0]][ranCoords[1]] != 2 and rplace[ranCoords[0]][ranCoords[1]] != 2: return ranCoords
			if player == 1 and rplace[ranCoords[0]][ranCoords[1]] != 2 and bplace[ranCoords[0]][ranCoords[1]] != 2: return ranCoords
			
#chooses a piece to move			
def randOrigin() :	
	global moveCoords
	global moveCheck
	global movingPris
	movingPris = False
	moveCheck = False
	
	options = []
	for i in range(0, len(board)) :
		for j in range(0, len(board[i])) : 
			if countPieces(board[i][j][turn % 2]) != 0 and board[i][j][turn % 2][3] != -1 : options.append([i, j])
	#chooses a square and prints it if slow.
	if options == [] :
		return
	moveCoords = options[random.randrange(0, len(options))]
	if isSlow :
		print(coordToAlg(moveCoords), end=' ')
		unn = input(" ")

	if board[moveCoords[0]][moveCoords[1]][(turn + 1) % 2][3] == -1 :
		if random.randrange(0,2) == 1 :
			check = True
			movingPris = True
		else:
			check = True
	
	moveCheck = True
	
	
#throws back a move randomly selected from the current options. Break only has 1 entry in the overall instead of the usual 3-4
def randMove() :
	move = possMoves[random.randrange(0, len(possMoves))]
	if move == "break":
		return alphBreaks[random.randrange(0, len(alphBreaks))]
	else : return move

def slow() :
	if isSlow :
		printBoard(board)
		uh = input("->")