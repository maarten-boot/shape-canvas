## Skeleton:

Create a tkinter app with a `menu bar` on top and `central frame` and a `status line` at the bottom.
In the `central frame` create a canvas.
The canvas color is initially `very light gray`.

## Initial menu:
On the canvas create a `popup menu` on a right `mouse click`.
In the `popup menu` allow the user to select 2 shapes: `rectangle`, `circle`.

## Draw a shape:
When the users selects a shape draw that shape on the canvas.
Use a static size for the initial draw.
For the rectangle use wide: 2cm by hight: 1cm.
For the circle use 2cm.
When the right mouse is released without making a selection remove the `popup menu`.

Give each shape a uuid.
Make the shapes initially filled with a fill color `light gray`.
Allow for the shapes to be `resizable` and `movable`.

## Shape Menu
Make a `popup menu for the shape`appear on right click when the mous is over the shape.
Make a Color entry in the popup for the shapes to change the fill color.
Make a `Properties` entry in the `popup menu for the shape`.
When selecting the `Properties` entry show a window with 2 fields: `Name` and `Description`.
The window can be closed with `Cancel` or `Save`.
On Cancel forger the data from the window.
When closing the `Properties` window with `Save` save the `Name` data as property `name` in the json state for the current shape
and the `Desription` data as property `description`.

## Remember state
Make internal json data structure where you remember the shapes, their color and position attributes and its uuid.
After each change save the json as the current state.
In the json add the program name and program version (initially v1) so that we can identfy the json later on.
Remember the position and size of each shape as position and size not as 2 positions.

Save the file in a `state.json` file in a hidden directory in the HOME location of the user.
The hidden directory is named after the basename of the app name wihout the file type.
for example: for a python app: basename(argv[0]) with the extension .py removed.

# Grid
When placing the shapes on the canvas use a grid of 1 cm by 1 cm and alighn the shapes to the grid.
Alow for a menu item to show and hide the grid.
Initially the grid is shown in `very light blue` as stippled lines.
Allow a entry in the menu for changing the grid color and line type.
Make the grid size changable.

## File save/load
Allow for the current state to be saved in a file selected by the user.
Allow for the user to load a previous saved json.
On loading a json file that does not have my program identifier display a warning and ignore the file.

## Command line args
Allow for the program to have command line arguments:
--clean to start without loading the saved state.
--file `<file path>` to load a json file on startup,
again if the file does not end in json or is not the proper file type: dispay a warning and ignore the file.

## Tabs
Use a tabbed fram for the central frame and place the canvas on the first tab. Label the tab: 'Canvas'.
Show a second tab with label 'Json'.
When selecting the Json tab show a non editable text window (with scrolling if the text is to large) with the current state.
Format the json with indent = 2.

## Depth
Allow for each shape on the canvas to have a depth and add a entry to the `shape popup menu` to change the depth layer.
Allow for Top, Bottom as ultimate positions (Tope being above all other shapes, Bottom meaning under all other shapes.
Allow for a shape to be lower (under another but not the lowest) and higher (above another but not the highest).
Add the depth level to the saved state.

## Properties Pane
When a shape is selected,
show the following properties in a window on the right side of the canvas and make them editable:
`Name`, `Description`.
