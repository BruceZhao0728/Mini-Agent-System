from config import MAX_OBS_CHARS

def clip(text: str, head: int = 6000, tail: int = 1500) -> str:
	'''
	Clip the output of tools that go into history to a limited length: keep the head and tail, and omit the middle.
	
	Args:
		text (str): The text to clip.
		head (int): The number of characters to keep from the start of the text. Default is 6000.
		tail (int): The number of characters to keep from the end of the text. Default is 1500.
	
	Returns:
		str: The clipped text, with the middle omitted if it exceeds MAX_OBS_CHARS
	'''
	s = str(text)
	if len(s) <= MAX_OBS_CHARS:
		return s
	return f"{s[:head]}\n…[Clipped {len(s) - head - tail} characters]…\n{s[-tail:]}"